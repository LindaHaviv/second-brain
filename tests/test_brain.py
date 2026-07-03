"""Real tests for the second brain — true cases against the live database (db.connect()) plus
pure-function unit tests. No LLM calls, so it's fast and deterministic.

  python tests/test_brain.py        # standalone runner (prints PASS/FAIL, exit code)
  pytest tests/test_brain.py        # also works
"""
import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

import db                # noqa: E402
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
    cur = c.cursor()
    # thresholds work for BOTH a real library and the 7-video tutorial sample
    for t, lo in [("posts", 5), ("content_chunks", 0), ("wiki_pages", 0),
                  ("page_sources", 0), ("semantic_memory", 0)]:
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert n >= lo, f"{t} has {n} rows (< {lo})"
    c.close()


def test_duality_views_readable():
    c = db.connect()
    cur = c.cursor()
    cur.execute("SELECT JSON_VALUE(data,'$.topic') FROM wiki_page_dv FETCH FIRST 1 ROWS ONLY")
    assert (cur.fetchone() or [None])[0], "wiki_page_dv not readable"
    cur.execute("SELECT JSON_VALUE(data,'$._id') FROM post_dv FETCH FIRST 1 ROWS ONLY")
    assert cur.fetchone(), "post_dv not readable"
    c.close()


def test_vector_search():
    c = db.connect()
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
    topic = content.list_topics(c)[0]
    p = content.get_wiki_page(c, topic)
    assert p and p["body"] and isinstance(p["citations"], list), "wiki page incomplete"
    c.close()


def test_get_post():
    c = db.connect()
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


def test_research_tool_errors_are_recoverable():
    """Malformed model tool input must return an error RESULT, not raise."""
    import research_agent
    c = db.connect()
    out = research_agent._run_tool(c, "get_post", {"post_id": None})
    assert isinstance(out, dict) and "error" in out
    out = research_agent._run_tool(c, "search_content", {})
    assert isinstance(out, dict) and "error" in out
    c.close()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for n, f in tests:
        try:
            f()
            print(f"  PASS  {n}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {n}: {str(e).splitlines()[0]}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

