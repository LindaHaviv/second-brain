"""Keep the brain current — and never let the derived layers go stale.

The rule: whenever new content lands, the *synthesized* layers must be rebuilt too. So this always
runs in order:  pull configured API sources  ->  refresh the wiki  ->  consolidate memory.
(The wiki + consolidation read only visibility='content', so private/off-topic never seep in.)

  ./.venv/bin/python scripts/sync.py

Schedule it (daily/weekly) as a macOS LaunchAgent for hands-off updates. Export-only sources
(ChatGPT/LinkedIn) and their one-time classify pass (scripts/classify_private.py) are separate —
run those when you drop a new export, then this folds the new content into the wiki + memory.
"""
import os
import pathlib
import subprocess
import sys

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "oracle" / ".env")   # so cred checks + child processes see the config
PY = sys.executable

# (label, argv, required_env) — a loader is skipped when its credential isn't configured.
STEPS = [
    ("Instagram",    [str(ROOT / "scripts" / "instagram.py")],          "IG_ACCESS_TOKEN"),
    ("Notion",       [str(ROOT / "scripts" / "notion.py")],             "NOTION_TOKEN"),
    ("Wiki refresh", [str(ROOT / "oracle" / "agent" / "wiki.py"), "--refresh"], None),
    ("Consolidate",  [str(ROOT / "scripts" / "consolidate.py")],        None),
]


def main():
    failed = []
    for label, argv, need in STEPS:
        if need and not os.environ.get(need):
            print(f"— skip {label} (no {need})")
            continue
        print(f"\n=== {label} ===", flush=True)
        if subprocess.run([PY, *argv]).returncode:
            print(f"  {label} FAILED — continuing")
            failed.append(label)
    print(f"\nsync complete." + (f" failed: {', '.join(failed)}" if failed else " all steps ok."))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
