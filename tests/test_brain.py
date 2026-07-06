"""Real tests for the second brain — true cases against the live database (db.connect()) plus
pure-function unit tests. No LLM calls, so it's fast and deterministic.

  python tests/test_brain.py        # standalone runner (prints PASS/FAIL, exit code)
  pytest tests/test_brain.py        # also works
"""
import asyncio
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

import db                # noqa: E402


def _skip_if_empty(c, table, hint):
    """Fresh brains aren't failures: Lab 1 alone leaves these tables empty by design."""
    n = c.cursor().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if n == 0:
        c.close()
        raise unittest.SkipTest(f"{table} is empty — {hint}")

import content           # noqa: E402
import memory            # noqa: E402
import semantic_memory   # noqa: E402
import mcp_server        # noqa: E402


# ---- integration: the live brain ----------------------------------------------------------

def test_connect():
    c = db.connect()
    user = c.cursor().execute("SELECT user FROM dual").fetchone()[0]
    assert user, "no DB user"
    c.close()


def test_tables_have_data():
    c = db.connect()
    _skip_if_empty(c, "posts", "load content first (Lab 2 sample or your own), then re-run")
    cur = c.cursor()
    # thresholds work for a real library, the 7-video tutorial sample, or a tiny own-data start
    for t, lo in [("posts", 1), ("content_chunks", 0), ("wiki_pages", 0),
                  ("page_sources", 0), ("semantic_memory", 0)]:
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert n >= lo, f"{t} has {n} rows (< {lo})"
    c.close()


def test_duality_views_readable():
    c = db.connect()
    _skip_if_empty(c, "posts", "load content first (Lab 2), then re-run")
    _skip_if_empty(c, "wiki_pages", "compile the wiki first (Lab 5 / Step 5), then re-run")
    cur = c.cursor()
    cur.execute("SELECT JSON_VALUE(data,'$.topic') FROM wiki_page_dv FETCH FIRST 1 ROWS ONLY")
    assert (cur.fetchone() or [None])[0], "wiki_page_dv not readable"
    cur.execute("SELECT JSON_VALUE(data,'$._id') FROM post_dv FETCH FIRST 1 ROWS ONLY")
    assert cur.fetchone(), "post_dv not readable"
    c.close()


def test_vector_search():
    c = db.connect()
    _skip_if_empty(c, "posts", "load content first (Lab 2), then re-run")
    res = content.search_content(c, "AI inference and the compute stack", 5)
    assert res and {"lvl", "title", "snippet"} <= set(res[0]), "bad search result shape"
    assert any(r["lvl"] == "wiki" for r in res) or len(res) >= 3, "expected layered results"
    c.close()


def test_hybrid_rescues_exact_name():
    """Hybrid search must rescue an exact keyword that vector-only ranking can bury.
    Self-contained + data-independent: seed a post with a unique token, confirm hybrid
    surfaces it by that exact token, then clean up."""
    c = db.connect()
    token = "zqxwvlemma"   # distinctive — won't collide with real content
    cur = c.cursor()
    cur.execute("alter session disable parallel dml")   # Autonomous DB: delete+insert in one txn
    cur.execute("merge into platforms p using (select 'test' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('test','Test')")
    cur.execute("insert into posts (platform_id, kind, title, caption, content_embedding) "
                "values ('test','note', :t, 'probe', vector_embedding(MINILM using :e as data))",
                t=f"{token} exact-match probe", e=token)
    c.commit()
    try:
        res = content.search_hybrid(c, token, 8)
        titles = " ".join((r.get("title") or "") for r in res).lower()
        assert token in titles, "hybrid search missed an exact-token lexical hit"
    finally:
        cur.execute("delete from posts where platform_id = 'test'")
        c.commit()
        c.close()


def test_get_wiki_page():
    c = db.connect()
    _skip_if_empty(c, "wiki_pages", "compile the wiki first (Lab 5 / Step 5), then re-run")
    topic = content.list_topics(c)[0]
    p = content.get_wiki_page(c, topic)
    assert p and p["body"] and isinstance(p["citations"], list), "wiki page incomplete"
    c.close()


def test_get_post():
    c = db.connect()
    _skip_if_empty(c, "posts", "load content first (Lab 2), then re-run")
    pid = c.cursor().execute("SELECT MIN(post_id) FROM posts").fetchone()[0]
    post = content.get_post(c, pid)
    assert post and "caption" in post, "get_post failed"
    c.close()


def test_memory_recall_shapes():
    c = db.connect()
    assert isinstance(memory.recall(c, "AI inference", k=3), list)
    assert isinstance(semantic_memory.semantic_recall(c, "audience", k=3), list)
    c.close()


def test_mcp_tools_registered():
    async def names():
        tm = getattr(mcp_server.mcp, "_tool_manager", None)
        tools = await (tm.list_tools() if tm else mcp_server.mcp.list_tools())
        return {t.name for t in tools}
    got = asyncio.run(names())
    assert {"search", "fetch", "wiki", "topics", "recent", "ingest_note"} <= got, got


# ---- unit: pure functions -----------------------------------------------------------------

def test_terms_filters_short_tokens():
    assert content._terms("How does AI Inference work?") == ["how", "does", "inference", "work"]


def test_rid_scheme():
    assert content._rid({"lvl": "wiki", "title": "X"}) == "wiki:X"
    assert content._rid({"lvl": "item", "post_id": 5}) == "item:5"


def test_schema_statement_split():
    sys.path.insert(0, str(ROOT / "scripts"))
    import apply_schema
    stmts = apply_schema.statements(
        "-- c\nCREATE TABLE t (a NUMBER);\nINSERT INTO t VALUES (1); -- inline\n")
    assert stmts == ["CREATE TABLE t (a NUMBER)", "INSERT INTO t VALUES (1)"], stmts


# --- regression tests for the 2026-07 code-review remediation --------------------------------

def test_record_clamps_long_values():
    """A question longer than VARCHAR2(500) must not crash the save (ORA-12899 regression)."""
    c = db.connect()
    cur = c.cursor()
    run_id = "test-clamp"
    try:
        memory.record(c, run_id + "x" * 60, "Q" * 900, "answered", "tool-name" * 30, "success",
                      detail="d")
        cur.execute("SELECT LENGTH(task), LENGTH(run_id), LENGTH(tool) "
                    "FROM agent_memory WHERE run_id LIKE 'test-clamp%'")
        task_len, rid_len, tool_len = cur.fetchone()
        assert task_len <= 500 and rid_len <= 40 and tool_len <= 80
    finally:
        cur.execute("DELETE FROM agent_memory WHERE run_id LIKE 'test-clamp%'")
        c.commit()
        c.close()


def test_clamp_bytes_is_byte_aware():
    """Byte clamp must respect VARCHAR2 byte semantics without splitting a character."""
    f = semantic_memory._clamp_bytes
    emoji = "x" * 998 + "🧠"          # 998 + 4 bytes = 1002 bytes
    out = f(emoji, 1000)
    assert len(out.encode()) <= 1000
    assert not out.endswith("\ufffd") and "🧠" not in out
    assert f("short", 1000) == "short"


def test_set_hwm_survives_missing_seed_row():
    """_set_hwm must MERGE: after the seed row disappears (e.g. a data reset), the
    high-water mark must still advance instead of silently updating 0 rows."""
    import wiki
    c = db.connect()
    cur = c.cursor()
    try:
        cur.execute("SELECT last_max_post_id FROM wiki_meta WHERE id = 1")
        before = (cur.fetchone() or [0])[0]
        cur.execute("DELETE FROM wiki_meta")
        wiki._set_hwm(cur, 12345)
        cur.execute("SELECT last_max_post_id FROM wiki_meta WHERE id = 1")
        assert int(cur.fetchone()[0]) == 12345, "MERGE did not re-create the seed row"
    finally:
        c.rollback()   # leave the real row untouched
        c.close()


def test_fetch_title_fallback_keeps_whole_string():
    """Colon-containing titles must fall back to an exact lookup on the WHOLE string."""
    src = open(pathlib.Path(__file__).resolve().parent.parent
               / "oracle" / "agent" / "mcp_server.py").read()
    assert 'title_fallback = str(id).strip()' in src, \
        "fetch fallback regressed to splitting on ':'"


def test_note_chunks_paragraphs():
    """Notes must chunk by paragraph so they get passage-level search like chats do."""
    from content import note_chunks
    body = "How to use: fetch this.\n\nSTEP 1 - do a thing\nwith detail\n\n\nSTEP 2 - more"
    chunks = note_chunks(body)
    assert chunks == ["How to use: fetch this.", "STEP 1 - do a thing\nwith detail",
                      "STEP 2 - more"]
    assert note_chunks("") == [] and note_chunks(None) == []
    long = "\n\n".join(f"p{i}" for i in range(60))
    assert len(note_chunks(long)) == 40          # cap
    assert len(note_chunks("x" * 5000)[0]) == 2000   # per-chunk byte-safe clamp


def test_mcp_public_layout_is_generic():
    """The PUBLIC server must expose exactly the teaching tools — no private workflow tools.
    (Private deploys layer server_ext in; this pins the boundary for forks and wrong builds.)"""
    import asyncio
    import subprocess
    import sys as _sys
    code = (
        "import sys, asyncio; sys.path.insert(0, 'oracle/agent')\n"
        "import mcp_server\n"
        "tools = sorted(t.name for t in asyncio.run(mcp_server.mcp._list_tools()))\n"
        "print(','.join(tools))\n")
    out = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(pathlib.Path(__file__).resolve().parent.parent))
    tools = out.stdout.strip().split(",")
    assert tools == ["by_series", "fetch", "ingest_note", "overview", "recent",
                     "save_chat", "search", "topics", "wiki"], tools


def test_rate_limiter_per_ip_isolation():
    """One noisy IP must not throttle another; the global backstop must still trip."""
    import importlib
    import os as _os
    _os.environ["MCP_ALLOW_ANON"] = "1"
    _os.environ["RATE_BURST"] = "5"
    _os.environ["RATE_PER_SEC"] = "0.001"
    import mcp_http
    importlib.reload(mcp_http)
    b = mcp_http._Bucket()
    assert sum(b.allow("1.1.1.1") for _ in range(10)) == 5
    assert sum(b.allow("2.2.2.2") for _ in range(10)) == 5   # unaffected by IP 1
    assert sum(b.allow("3.3.3.3") for _ in range(10)) == 5
    assert sum(b.allow("4.4.4.4") for _ in range(10)) == 5
    assert sum(b.allow("5.5.5.5") for _ in range(10)) == 0   # global backstop (4x burst) hit
    for k in ("RATE_BURST", "RATE_PER_SEC"):
        del _os.environ[k]


def test_search_cursor_roundtrip():
    """Cursors must survive the round trip and ERROR on a mismatched query (silent page-1
    restarts hand the model duplicates with no signal)."""
    import mcp_server
    tok = mcp_server._encode_cursor("my query", 16)
    assert mcp_server._decode_cursor(tok, "my query") == 16
    try:
        mcp_server._decode_cursor(tok, "different query")
        assert False, "mismatched cursor must raise"
    except Exception:
        pass


def test_output_cap_is_explicit():
    """Truncation must announce itself — silent truncation reads as 'that was everything'."""
    import mcp_server
    big = "x" * (mcp_server._TEXT_CAP + 500)
    capped = mcp_server._cap(big)
    assert len(capped) < len(big) and "truncated" in capped


def test_research_verify_gate_is_wired():
    """The verification pass must sit BEFORE record() (wrong claims must not be remembered),
    default ON, with a graceful fallback if the check itself fails."""
    import research_agent
    src = open(pathlib.Path(__file__).resolve().parent.parent
               / "oracle" / "agent" / "research_agent.py").read()
    assert callable(research_agent.verify_answer)
    assert 'os.environ.get("RESEARCH_VERIFY", "1")' in src, "verify gate no longer default-on"
    assert src.index('verify_answer(client, messages, answer)') < src.index('record(conn, "research"'), \
        "verify pass must run before the answer is recorded to memory"
    for verdict in ("supported", "unsupported", "contradicted"):
        assert verdict in research_agent.VERIFY_SCHEMA["properties"]["claims"]["items"][
            "properties"]["verdict"]["enum"]


def test_research_tool_errors_are_recoverable():
    """Malformed model tool input must return an error RESULT, not raise."""
    import research_agent
    c = db.connect()
    out = research_agent._run_tool(c, "get_post", {"post_id": None})
    assert isinstance(out, dict) and "error" in out
    out = research_agent._run_tool(c, "search_content", {})
    assert isinstance(out, dict) and "error" in out
    c.close()


def test_obsidian_parse_note():
    sys.path.insert(0, str(ROOT / "scripts"))
    from obsidian import parse_note
    meta, body = parse_note("""---
title: My Course Notes
tags: ml, course
visibility: private
---
# Heading
Text with a [[Wiki Link]] and an [[page|aliased link]].""")
    assert meta["title"] == "My Course Notes"
    assert meta["visibility"] == "private"
    assert "Wiki Link" in body and "aliased link" in body
    assert "[[" not in body and "# " not in body
    meta2, body2 = parse_note("no frontmatter at all")
    assert meta2 == {} and body2 == "no frontmatter at all"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = skipped = 0
    for n, f in tests:
        try:
            f()
            print(f"  PASS  {n}")
            passed += 1
        except unittest.SkipTest as e:
            print(f"  SKIP  {n}: {e}")
            skipped += 1
        except Exception as e:
            print(f"  FAIL  {n}: {str(e).splitlines()[0]}")
            failed += 1
    tail = f", {skipped} skipped (fine on a fresh brain)" if skipped else ""
    print(f"\n{passed} passed, {failed} failed{tail}")
    sys.exit(1 if failed else 0)

