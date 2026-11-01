"""Collect step (Claude Code): your local session transcripts -> the brain.

PRIVACY/SECURITY (best practice): your Claude Code transcripts can contain secrets (they
include tool output + system notes from your sessions). So this loader:
  1. ingests ONLY your user/assistant `text` (skips tool_result / tool_use / system) — that's
     where key dumps live, so skipping them removes most risk;
  2. REDACTS known secret shapes (sk-ant-, ntn_, secret_, AWS, GitHub, ...) -> [REDACTED];
  3. is LOCAL only (reads ~/.claude/projects, loads into local Oracle).
Still: rotate any keys that were ever in your sessions — that's the real safety net.

INCREMENTAL: a state file (exports/claude_code_state.json) remembers each transcript's
size+mtime and post_id. Only new or changed sessions are (re)embedded and only vanished
ones are deleted; unchanged sessions keep their post_id, so the wiki does not see hundreds
of 'new posts' every morning and this step takes seconds instead of ~25 minutes.
Run from repo root:  ./.venv/bin/python scripts/claude_code.py   (--full forces a rebuild)
"""
import json
import os
import pathlib

import oracledb
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "oracle" / ".env")
oracledb.defaults.fetch_lobs = False
PROJECTS = pathlib.Path.home() / ".claude" / "projects"
STATE = ROOT / "exports" / "claude_code_state.json"

# one shared list for every loader (scripts/redaction.py)
from redaction import redact  # noqa: E402


def connect():
    # single source of truth: env-driven, wallet-aware, NO password fallback (oracle/agent/db.py)
    import sys
    sys.path.insert(0, str(ROOT / "oracle" / "agent"))
    import db
    return db.open_connection()


def messages_of(path):
    """Extract (role, redacted_text) for user/assistant text only — skip tool/system blocks."""
    out = []
    for ln in open(path):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        m = d.get("message")
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        c = m.get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            text = " ".join(b.get("text", "") for b in c
                            if isinstance(b, dict) and b.get("type") == "text")
        else:
            text = ""
        text = redact(text.strip())
        if text:
            out.append((m["role"], text))
    return out


def chunks_of(msgs, size=1500):
    out, buf = [], ""
    for role, text in msgs:
        piece = f"{role}: {text}"
        if buf and len(buf) + len(piece) + 1 > size:
            out.append(buf)
            buf = piece
        else:
            buf = f"{buf}\n{piece}" if buf else piece
    if buf:
        out.append(buf)
    return out


def session_url(path):
    """Stable key for a transcript: project dir + file stem (posts.url, was null)."""
    return f"claude-code://{path.parent.name}/{path.stem}"


def plan(current, previous):
    """Pure: current = {url: (size, mtime)}, previous = {url: {"size","mtime","post_id"}}.
    Returns (to_load, to_delete_post_ids, unchanged_count)."""
    to_load, to_delete, unchanged = [], [], 0
    for url, (size, mtime) in current.items():
        prev = previous.get(url)
        if prev and prev.get("size") == size and abs(prev.get("mtime", -1) - mtime) < 1e-6 \
                and prev.get("post_id"):
            unchanged += 1
            continue
        to_load.append(url)
        if prev and prev.get("post_id"):
            to_delete.append(int(prev["post_id"]))
    for url, prev in previous.items():
        if url not in current and prev.get("post_id"):
            to_delete.append(int(prev["post_id"]))
    return to_load, to_delete, unchanged


def _load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def main():
    import sys
    if not PROJECTS.exists():
        print("no ~/.claude/projects found")
        return
    full = "--full" in sys.argv
    files = {session_url(f): f for f in sorted(PROJECTS.glob("*/*.jsonl"))}
    current = {u: (f.stat().st_size, f.stat().st_mtime) for u, f in files.items()}
    previous = {} if full else _load_state()
    to_load, to_delete, unchanged = plan(current, previous)

    conn = connect()
    cur = conn.cursor()
    cur.execute("alter session disable parallel dml")   # Autonomous: delete+insert in one txn
    cur.execute("merge into platforms p using (select 'claude_code' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('claude_code','Claude Code sessions')")
    if not previous:
        # first incremental run (or --full): the old loader left url NULL, so nothing can be
        # matched; rebuild once, then every later run is a delta.
        cur.execute("delete from content_chunks where post_id in "
                    "(select post_id from posts where platform_id='claude_code')")
        cur.execute("delete from posts where platform_id='claude_code'")
        print("  (no state yet — one full rebuild, incremental from tomorrow)")
    elif to_delete:
        binds = {f"p{i}": v for i, v in enumerate(to_delete)}
        inl = ",".join(f":p{i}" for i in range(len(binds)))
        cur.execute(f"delete from content_chunks where post_id in ({inl})", **binds)
        cur.execute(f"delete from posts where post_id in ({inl})", **binds)

    n, total_chunks, state = 0, 0, dict(previous)
    for url in to_load:
        f = files[url]
        msgs = messages_of(f)
        state.pop(url, None)
        if not msgs:
            continue
        first_user = next((t for r, t in msgs if r == "user"), msgs[0][1])
        title = (first_user[:120] or "Claude Code session").replace("\n", " ")
        overview = first_user[:1500]
        outid = cur.var(oracledb.NUMBER)
        cur.execute(
            """
            insert into posts (platform_id, kind, title, caption, url, published_at, content_embedding)
            values ('claude_code', 'session', :title, :caption, :url, null,
                    vector_embedding(MINILM using :emb as data))
            returning post_id into :outid
            """,
            title=title, caption=overview, url=url, emb=(title + ". " + overview)[:3000], outid=outid,
        )
        post_id = int(outid.getvalue()[0])
        rows = [(post_id, i, ch, ch[:3000]) for i, ch in enumerate(chunks_of(msgs))]
        if rows:   # one round-trip per session instead of one per chunk
            cur.executemany(
                """insert into content_chunks (post_id, seq, chunk, embedding)
                   values (:1, :2, :3, vector_embedding(MINILM using :4 as data))""", rows)
        total_chunks += len(rows)
        size, mtime = current[url]
        state[url] = {"size": size, "mtime": mtime, "post_id": post_id}
        n += 1
    for url in list(state):
        if url not in current:
            state.pop(url)
    conn.commit()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=0, sort_keys=True))
    print(f"loaded {n} changed/new Claude Code sessions + {total_chunks} (redacted) chunks; "
          f"{unchanged} unchanged, {len(to_delete)} removed")
    conn.close()


if __name__ == "__main__":
    main()
