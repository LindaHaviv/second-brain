"""Claude Code learnings sweep: what the OTHER Claude Code folders learned about you -> the brain.

The Claude Code loader (scripts/claude_code.py) already stores every session transcript, from
every folder, as searchable chunks. But a transcript is where feedback goes to get buried:
"I don't do hooks like that" is one line among thousands, ranked below notes, found only by
someone who already knows to look. Meanwhile each OTHER project folder (an editing engine, a
tool you bought, a side repo) keeps its own private memory about you that this brain never
sees. This sweep closes both gaps, ONE WAY: those folders -> brain. It never writes anything
into them.

  1. MEMORY IMPORT — every `~/.claude/projects/<folder>/memory/*.md` outside this repo's own
     folder becomes a note in the brain (platform 'note', url `claude-memory://<folder>/<name>`
     so re-runs UPDATE rather than duplicate; unchanged files are skipped). Deterministic, no LLM.
  2. FEEDBACK DISTILL — new/changed session transcripts in those folders go through the LLM
     once: it extracts EXPLICIT feedback, preferences, and experiments the user stated (never
     inferred taste), each with the quote it rests on. Each learning becomes a note
     (`claude-session://<folder>/<session>#<n>`), skipped when a near-identical note already
     exists (in-DB vector distance). Sessions are tracked by content hash in
     exports/claude_code_sweep_state.json so each is distilled once.

Privacy (AGENTS.md rule 2): user text only (tool output and system blocks skipped), run through
the shared redactor, and every note is screened by the deterministic deny-list
(oamp_memory.violates_privacy): a hit lands as visibility='business', out of every read path.
Direction of data is the point: the brain learns from the editor; the editor is not told
anything by this script.

Report (rule 5): private/reports/claude-code-sweep-<date>.md (surfaced by the chief-of-staff
brief) + exports/claude_code_sweep.json. Scheduled by scripts/sync.py after the Claude Code
loader and before the wiki refresh, so new learnings feed the same day's wiki.

  ./.venv/bin/python scripts/claude_code_sweep.py              # full run
  ./.venv/bin/python scripts/claude_code_sweep.py --dry-run    # report only, writes nothing
  ./.venv/bin/python scripts/claude_code_sweep.py --no-llm     # memory import only

Env: SWEEP_INCLUDE (comma list of folder-name substrings to restrict to), SWEEP_EXCLUDE (extra
substrings to skip; the brain's own folder and worktrees/scratch workspaces are always skipped),
SWEEP_MIN_USER_CHARS (default 400: shorter sessions carry no feedback worth an LLM call).
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / "oracle" / ".env")

from redaction import redact  # noqa: E402

PROJECTS = pathlib.Path(os.environ.get("CLAUDE_PROJECTS_DIR") or (pathlib.Path.home() / ".claude" / "projects"))
STATE = ROOT / "exports" / "claude_code_sweep_state.json"
REPORT_JSON = ROOT / "exports" / "claude_code_sweep.json"
REPORTS_DIR = ROOT / "private" / "reports"
MIN_USER_CHARS = int(os.environ.get("SWEEP_MIN_USER_CHARS", "400"))
DUP_DISTANCE = float(os.environ.get("SWEEP_DUP_DISTANCE", "0.18"))   # cosine; near-identical wording
MAX_SESSION_CHARS = 60_000   # per LLM call; the user's own words, so this is generous

# folders that are never "another project": this repo, its worktrees, and the desktop app's
# throwaway scratch workspaces (one-off chats, not a place that learns about the user)
ALWAYS_SKIP = ("-claude-worktrees-", "scratch-workspaces")


def project_slug(path_or_name):
    """`-Users-x-Projects-Reels-Editing-Engine` -> `Reels-Editing-Engine` (the leaf folder)."""
    name = pathlib.Path(str(path_or_name)).name
    parts = [p for p in name.split("-") if p]
    # the encoded path replaces '/' with '-', so the leaf is ambiguous when the folder name
    # itself has dashes; keep everything after the last well-known parent directory
    for anchor in ("Projects", "Development", "Documents", "Desktop", "Code", "src"):
        if anchor in parts:
            tail = parts[parts.index(anchor) + 1:]
            if tail:
                return "-".join(tail)
    return "-".join(parts[-3:]) if parts else name


def own_folder_name():
    """The encoded ~/.claude/projects folder name for THIS repo (skip it: it's the brain)."""
    return str(ROOT).replace("/", "-").replace(" ", "-")


def clone_marker():
    """Sibling clones/demos of this repo ('second-brain-demo' next to 'Second Brain Main') hold
    memory about the brain itself, not about the user: skip them. The marker is the repo
    name's first two words, dashed and lowercased."""
    words = [w for w in re.split(r"[\s_-]+", ROOT.name.lower()) if w]
    return "-".join(words[:2]) if len(words) >= 2 else ROOT.name.lower()


def is_candidate(folder, include=None, exclude=None, root_name=None, marker=None):
    """Pure filter: which project folders this sweep reads (case-insensitive substrings)."""
    name = pathlib.Path(str(folder)).name
    low = name.lower()
    if low == (root_name or own_folder_name()).lower() or any(s in low for s in ALWAYS_SKIP):
        return False
    if (marker or clone_marker()) in low:
        return False
    if any(s and s.lower() in low for s in (exclude or [])):
        return False
    if include:
        return any(s and s.lower() in low for s in include)
    return True


# ---- 1. memory files ------------------------------------------------------------------------

def parse_memory_file(text):
    """Frontmatter (name, description, type) + body. Tolerant: a file without frontmatter is
    just a body with its filename as name."""
    meta, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        for ln in m.group(1).splitlines():
            mm = re.match(r"^\s*(name|description|type)\s*:\s*(.+?)\s*$", ln)
            if mm:
                meta[mm.group(1)] = mm.group(2).strip().strip('"').strip("'")
    return meta, body.strip()


def memory_note(folder, path, text):
    """One memory file -> (title, body, url). Title = the one-line description the agent wrote
    (what the user would search for), prefixed with the lane so the source is obvious."""
    meta, body = parse_memory_file(text)
    slug = project_slug(folder)
    name = meta.get("name") or path.stem
    desc = meta.get("description") or name.replace("-", " ")
    kind = meta.get("type", "")
    title = f"{slug} learned: {desc}"[:1000]
    head = f"Source: Claude Code memory of the {slug} project ({path.name}" + (f", type {kind}" if kind else "") + ").\n\n"
    return title, head + redact(body), f"claude-memory://{slug}/{name}"


def iter_memory_files(folder):
    mem = folder / "memory"
    if not mem.is_dir():
        return []
    return sorted(p for p in mem.glob("*.md") if p.name != "MEMORY.md")


# ---- 2. session transcripts -----------------------------------------------------------------

def user_text_of(path):
    """The user's own messages from a session jsonl, redacted, in order. Assistant text is
    skipped on purpose: we want what SHE said, not what the editor proposed."""
    out = []
    with open(path) as f:
        for ln in f:
            try:
                d = json.loads(ln)
            except Exception:
                continue
            m = d.get("message")
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                text = c
            elif isinstance(c, list):
                text = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
            else:
                continue
            text = text.strip()
            # Claude Code wraps harness notices in <system-reminder>; drop them and tool-result echoes
            text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()
            if text and not text.startswith(("<local-command", "<command-")):
                out.append(redact(text))
    return out


SCHEMA = {
    "type": "object",
    "properties": {
        "learnings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "<= 90 chars, the rule itself, searchable"},
                    "rule": {"type": "string", "description": "the preference/feedback/experiment, 1-3 sentences, her words paraphrased faithfully"},
                    "why": {"type": "string", "description": "her stated reason, or '' if none given"},
                    "quote": {"type": "string", "description": "the exact user sentence(s) this rests on"},
                    "kind": {"type": "string", "enum": ["preference", "correction", "experiment", "decision"]},
                    "topic": {"type": "string", "description": "2-4 words, e.g. 'hooks', 'captions', 'music', 'cut pacing'"},
                },
                "required": ["title", "rule", "why", "quote", "kind", "topic"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["learnings"],
    "additionalProperties": False,
}

SYSTEM = """You extract DURABLE, EXPLICIT learnings about a creator from her own messages to an AI
assistant in a project folder (often a video-editing tool). Return only what she STATED:
feedback ("don't do X", "I like Y better"), standing preferences, corrections, named experiments
("let's try Z this week"), and decisions. Never infer taste from silence, never restate the
assistant's suggestions as her rules, never include one-off task instructions ("cut this clip at
0:42") or anything about credentials, money, health, family, or other people's private matters.
Each learning stands alone and carries the exact quote it rests on. Prefer 0-6 learnings; an
empty list is the right answer for a session that is just task traffic."""


def distill(slug, texts):
    """LLM pass over one session's user messages -> list of learning dicts (may be empty)."""
    import llm
    joined = "\n\n".join(f"- {t}" for t in texts)[:MAX_SESSION_CHARS]
    prompt = (f"Project folder: {slug}. The creator's messages in this session, in order:\n\n{joined}\n\n"
              "Extract the durable learnings as JSON.")
    out = llm.structured(SYSTEM, prompt, SCHEMA, max_tokens=8000)
    items = []
    for it in out.get("learnings", []) or []:
        if not it.get("title") or not it.get("rule") or not it.get("quote"):
            continue
        items.append(it)
    return items


def learning_note(slug, session_id, n, it, when):
    title = f"{slug} feedback: {it['title'].strip()}"[:1000]
    body = (f"{it['rule'].strip()}\n\n"
            + (f"Why: {it['why'].strip()}\n\n" if it.get("why", "").strip() else "")
            + f"Her words: \"{it['quote'].strip()}\"\n\n"
            + f"Source: Claude Code session in the {slug} project, {when} ({it.get('kind', 'preference')}, {it.get('topic', '')}).")
    return title, redact(body), f"claude-session://{slug}/{session_id}#{n}"


# ---- DB -------------------------------------------------------------------------------------

def connect():
    import db
    return db.open_connection()


def visibility_for(text):
    from oamp_memory import violates_privacy
    return "business" if violates_privacy(text) else "content"


def existing_note(cur, url):
    cur.execute("SELECT post_id, caption FROM posts WHERE platform_id='note' AND url=:u", u=url)
    return cur.fetchone()


def near_duplicate(cur, title, body):
    """A learning already in the brain in other words (other session, or the memory file that
    recorded the same rule) -> skip. Distance over the note-level embedding, content scope only
    is NOT required here: a private duplicate is still a duplicate."""
    cur.execute(
        "SELECT title, VECTOR_DISTANCE(content_embedding, VECTOR_EMBEDDING(MINILM USING :e AS DATA), COSINE) d "
        "FROM posts WHERE platform_id='note' AND url LIKE 'claude-%' AND content_embedding IS NOT NULL "
        "ORDER BY d FETCH FIRST 1 ROWS ONLY", e=f"{title}. {body}"[:3000])
    row = cur.fetchone()
    if row and row[1] is not None and float(row[1]) < DUP_DISTANCE:
        return row[0]
    return None


def upsert_note(cur, title, body, url, published_at=None):
    """Insert or replace a note by url. Returns 'added' | 'updated' | 'unchanged'."""
    import content
    row = existing_note(cur, url)
    if row:
        if (row[1] or "") == body:
            return "unchanged"
        cur.execute("DELETE FROM content_chunks WHERE post_id=:p", p=row[0])
        cur.execute("DELETE FROM posts WHERE post_id=:p", p=row[0])
    vis = visibility_for(f"{title}\n{body}")
    outid = cur.var(int)
    cur.execute(
        "INSERT INTO posts (platform_id, kind, title, caption, url, published_at, visibility, content_embedding) "
        "VALUES ('note', 'note', :t, :c, :u, :d, :v, VECTOR_EMBEDDING(MINILM USING :e AS DATA)) "
        "RETURNING post_id INTO :outid",
        t=title, c=body[:8000], u=url, d=published_at, v=vis, e=f"{title}. {body}"[:3000], outid=outid)
    pid = int(outid.getvalue()[0])
    for i, para in enumerate(content.note_chunks(body)):
        cur.execute("INSERT INTO content_chunks (post_id, seq, chunk, embedding) "
                    "VALUES (:p, :s, :c, VECTOR_EMBEDDING(MINILM USING :e AS DATA))",
                    p=pid, s=i, c=para, e=para[:3000])
    return "updated" if row else "added"


# ---- state + report -------------------------------------------------------------------------

def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"sessions": {}}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True))


def write_report(summary, dry_run):
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(summary, indent=1))
    if dry_run:
        return None
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    day = summary["run_at"][:10]
    p = REPORTS_DIR / f"claude-code-sweep-{day}.md"
    lines = [f"# Claude Code learnings sweep, {day}", "",
             f"Folders read: {', '.join(summary['folders']) or 'none'}", "",
             f"- memory notes: {summary['memory']['added']} added, {summary['memory']['updated']} updated, "
             f"{summary['memory']['unchanged']} unchanged",
             f"- sessions distilled: {summary['sessions']['distilled']} (skipped {summary['sessions']['skipped_seen']} already seen, "
             f"{summary['sessions']['skipped_short']} too short)",
             f"- learnings: {summary['learnings']['added']} added, {summary['learnings']['duplicates']} already known, "
             f"{summary['learnings']['private']} filed private", ""]
    if summary["new_learnings"]:
        lines += ["## New this run", ""]
        for it in summary["new_learnings"]:
            lines.append(f"- **{it['title']}** ({it['slug']}, {it['kind']}): {it['rule']}")
            lines.append(f"  Her words: \"{it['quote']}\"")
        lines.append("")
    if summary["errors"]:
        lines += ["## Errors", ""] + [f"- {e}" for e in summary["errors"]] + [""]
    lines += ["Nothing here was written back to the source folders. Prune a wrong learning by deleting its note "
              "(the url starts with `claude-memory://` or `claude-session://`); it will not come back unless the "
              "source changes."]
    p.write_text("\n".join(lines))
    return p


# ---- main -----------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    ap.add_argument("--no-llm", action="store_true", help="memory import only, skip the distill pass")
    args = ap.parse_args(argv)

    include = [s.strip() for s in os.environ.get("SWEEP_INCLUDE", "").split(",") if s.strip()]
    exclude = [s.strip() for s in os.environ.get("SWEEP_EXCLUDE", "").split(",") if s.strip()]
    summary = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "dry_run": args.dry_run,
               "folders": [], "memory": {"added": 0, "updated": 0, "unchanged": 0},
               "sessions": {"distilled": 0, "skipped_seen": 0, "skipped_short": 0},
               "learnings": {"added": 0, "duplicates": 0, "private": 0}, "new_learnings": [], "errors": []}
    if not PROJECTS.exists():
        print(f"no {PROJECTS} found")
        write_report(summary, args.dry_run)
        return 0

    folders = [d for d in sorted(PROJECTS.iterdir()) if d.is_dir() and is_candidate(d, include, exclude)]
    summary["folders"] = [project_slug(d) for d in folders]
    state = load_state()
    conn = None if args.dry_run else connect()
    cur = conn.cursor() if conn else None
    if cur:
        cur.execute("alter session disable parallel dml")
        cur.execute("MERGE INTO platforms p USING (SELECT 'note' id FROM dual) s ON (p.platform_id=s.id) "
                    "WHEN NOT MATCHED THEN INSERT (platform_id, display_name) VALUES ('note','Quick notes')")

    for folder in folders:
        slug = project_slug(folder)
        # 1. memory files
        for path in iter_memory_files(folder):
            try:
                title, body, url = memory_note(folder, path, path.read_text())
                when = datetime.datetime.fromtimestamp(path.stat().st_mtime)
                if cur:
                    outcome = upsert_note(cur, title, body, url, when)
                else:
                    outcome = "added"
                summary["memory"][outcome] += 1
                if outcome != "unchanged":
                    print(f"  memory {outcome}: {title[:90]}")
            except Exception as e:
                summary["errors"].append(f"{slug}/{path.name}: {e}")
        if cur:
            conn.commit()
        if args.no_llm:
            continue
        # 2. sessions
        for path in sorted(folder.glob("*.jsonl")):
            sid = path.stem
            try:
                texts = user_text_of(path)
            except Exception as e:
                summary["errors"].append(f"{slug}/{sid}: {e}")
                continue
            digest = hashlib.sha256("\n".join(texts).encode()).hexdigest()[:16]
            key = f"{folder.name}/{sid}"
            if state["sessions"].get(key) == digest:
                summary["sessions"]["skipped_seen"] += 1
                continue
            if sum(len(t) for t in texts) < MIN_USER_CHARS:
                summary["sessions"]["skipped_short"] += 1
                state["sessions"][key] = digest
                continue
            if args.dry_run:
                summary["sessions"]["distilled"] += 1
                print(f"  would distill {slug}/{sid[:8]} ({len(texts)} user messages)")
                continue
            when = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            try:
                items = distill(slug, texts)
            except Exception as e:
                summary["errors"].append(f"{slug}/{sid}: distill failed: {e}")
                continue   # not marked seen: retried next run
            summary["sessions"]["distilled"] += 1
            for n, it in enumerate(items):
                title, body, url = learning_note(slug, sid, n, it, when.date().isoformat())
                dup = near_duplicate(cur, title, body)
                if dup:
                    summary["learnings"]["duplicates"] += 1
                    continue
                outcome = upsert_note(cur, title, body, url, when)
                if outcome == "added":
                    summary["learnings"]["added"] += 1
                    if visibility_for(f"{title}\n{body}") == "business":
                        summary["learnings"]["private"] += 1
                    else:
                        summary["new_learnings"].append({"slug": slug, "title": it["title"], "rule": it["rule"],
                                                         "quote": it["quote"][:300], "kind": it.get("kind", "")})
                    print(f"  learning: {title[:90]}")
            state["sessions"][key] = digest
            conn.commit()
            save_state(state)

    if conn:
        conn.commit()
        conn.close()
        save_state(state)
    report = write_report(summary, args.dry_run)
    m, s, l = summary["memory"], summary["sessions"], summary["learnings"]
    print(f"\nsweep {'(dry run) ' if args.dry_run else ''}over {len(folders)} folder(s): "
          f"memory {m['added']}+{m['updated']} changed / {m['unchanged']} same; "
          f"sessions {s['distilled']} distilled; learnings {l['added']} new, {l['duplicates']} known"
          + (f"; {len(summary['errors'])} session error(s) — see the report" if summary["errors"] else "")
          + (f"; report {report}" if report else ""))
    return exit_code(summary)


def exit_code(summary):
    """Pure (unit-tested): the step's exit status. A sweep that RAN and wrote its report
    exits 0 even when some sessions failed to distill — those stay in the report's
    "## Errors" section and are retried next run (they are never marked seen). Only a
    fatal failure (nothing could run: no folders, DB or LLM down before any work) is a
    failed step; before this rule one bad session a day showed the whole loop as broken
    in sync_status, the watchdog, and Athena's system desk for a week."""
    return 1 if summary.get("fatal") else 0


if __name__ == "__main__":
    sys.exit(main())
