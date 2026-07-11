"""Keep the brain current — and never let the derived layers go stale.

The rule: whenever new content lands, the *synthesized* layers must be rebuilt too. So this always
runs in order:  pull configured API sources  ->  classify if needed  ->  refresh the wiki  ->
consolidate memory. (The wiki + consolidation read only visibility='content', so private/off-topic
never seep in.)

  ./.venv/bin/python scripts/sync.py

Schedule it (daily/weekly) as a macOS LaunchAgent for hands-off updates. Export-only sources
(ChatGPT/LinkedIn) are separate — drop a new export, run its loader, and this folds the new
content in. SAFETY NET: re-importing a chat export RESETS visibility tags (private chats would
be searchable again) — sync detects that state and reruns the classifier automatically.
"""
import os
import pathlib
import subprocess
import sys

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "oracle" / ".env")   # so cred checks + child processes see the config
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
PY = sys.executable

# which credential the configured LLM provider needs (ollama needs none) — the
# classify/wiki/consolidate steps are provider-agnostic via oracle/agent/llm.py
LLM_KEY = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(
    os.environ.get("LLM_PROVIDER", "anthropic").lower())

# (label, argv, required_env) — a loader is skipped when its credential isn't configured.
STEPS = [
    ("Instagram",    [str(ROOT / "scripts" / "instagram.py")],          "IG_ACCESS_TOKEN"),
    ("Obsidian",     [str(ROOT / "scripts" / "obsidian.py")],           "OBSIDIAN_VAULT"),
    ("Google Drive", [str(ROOT / "scripts" / "gdrive.py")],             "GDRIVE_KEY"),
    ("Notion",       [str(ROOT / "scripts" / "notion.py")],             "NOTION_TOKEN"),
    ("Claude Code",  [str(ROOT / "scripts" / "claude_code.py")],        None),
    ("Wiki refresh", [str(ROOT / "oracle" / "agent" / "wiki.py"), "--refresh"], None),
    ("Consolidate",  [str(ROOT / "scripts" / "consolidate.py")],        None),
]

# ship path only: sweep the package's extracted memories against the structural
# privacy deny-list (the prompt guard filters; this enforces). No-op on custom.
def resolve_memory_backend(env):
    """Mirrors research_agent._resolve_backend: unset -> oamp, except ollama -> custom.
    Parity is enforced by tests/test_brain.py::test_backend_resolution_parity."""
    return (env.get("MEMORY_BACKEND") or (
        "custom" if env.get("LLM_PROVIDER", "anthropic").lower() == "ollama"
        else "oamp")).lower()


if resolve_memory_backend(os.environ) == "oamp":
    STEPS.append(("OAMP privacy sweep", [str(ROOT / "scripts" / "oamp_sweep.py")], None))


def _tags_look_reset():
    """True when chat posts exist but NONE are tagged business/archived — the signature of a
    fresh chat re-import (loaders reset visibility to the default). In that state private chats
    are searchable again, so classification must run BEFORE the wiki/memory rebuild."""
    try:
        import db
        conn = db.connect()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM posts WHERE platform_id IN "
                        "('chatgpt','claude','claude_code')")
            chats = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM posts WHERE platform_id IN "
                        "('chatgpt','claude','claude_code') AND visibility IN "
                        "('business','archived')")
            tagged = cur.fetchone()[0]
        conn.close()
        return chats > 50 and tagged == 0
    except Exception as e:
        print(f"  (classify guard check failed: {e})")
        return False


def _write_status(results):
    """Append this run's per-step outcomes to exports/sync_status.json (last 30 runs kept).
    The loop-health report reads this — a step that fails or skips REPEATEDLY should
    become a headline, not a log line."""
    import datetime
    import json
    path = ROOT / "exports" / "sync_status.json"
    try:
        history = json.loads(path.read_text()) if path.exists() else []
    except Exception:
        history = []
    history.append({"run_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "steps": results})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history[-30:], indent=1))


def _run_step(label, argv, results):
    import time
    t0 = time.time()
    env = dict(os.environ, LOOP_LABEL=label)   # tag the loop ledger per step
    rc = subprocess.run([PY, *argv], env=env).returncode
    results.append({"label": label, "status": "fail" if rc else "ok",
                    "seconds": round(time.time() - t0, 1)})
    return rc


def main():
    results = []
    # FIRST: pick up any new chat-export zips (drop-zip-and-forget). Runs before the
    # tags-reset check so a fresh import gets classified in THIS run, not tomorrow's.
    print("=== Chat exports (watch folder) ===", flush=True)
    _run_step("Chat exports", [str(ROOT / "scripts" / "ingest_exports.py")], results)

    steps = list(STEPS)
    if _tags_look_reset():
        print("!! chat visibility tags look RESET (fresh import?) — classifying before rebuild")
        steps.insert(2, ("Classify (safety net)",
                         [str(ROOT / "scripts" / "classify_private.py"), "--apply"],
                         LLM_KEY))
    failed = []
    for label, argv, need in steps:
        if need and not os.environ.get(need):
            print(f"— skip {label} (no {need})")
            results.append({"label": label, "status": "skip", "seconds": 0})
            continue
        print(f"\n=== {label} ===", flush=True)
        if _run_step(label, argv, results):
            print(f"  {label} FAILED — continuing")
            failed.append(label)
    _write_status(results)
    print(f"\nsync complete." + (f" failed: {', '.join(failed)}" if failed else " all steps ok."))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
