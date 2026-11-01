"""Scopes: pure logic + the middleware with a fake token. No DB, no network.
  ./.venv/bin/python tests/test_scopes.py"""
import asyncio
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "oracle" / "agent"))
import scopes as sc  # noqa: E402
from scope_middleware import ScopeMiddleware  # noqa: E402

ALL = ["search", "fetch", "wiki", "topics", "recent", "related", "by_series", "overview",
       "source_status", "list_agents", "ingest_note", "save_chat",
       "backlog_add", "backlog_review", "diagram_from_video_url", "some_future_tool"]


def test_owner_sees_everything():
    assert sc.filter_tools(ALL, {"owner"}) == ALL


def test_content_read_is_read_only():
    got = sc.filter_tools(ALL, {"content.read"})
    assert "search" in got and "wiki" in got
    assert "ingest_note" not in got and "backlog_add" not in got and "some_future_tool" not in got


def test_pearl_scope_reads_and_writes_notes_only():
    got = set(sc.filter_tools(ALL, {"content.read", "notes.write"}))
    assert {"search", "ingest_note", "save_chat"} <= got
    assert not got & {"backlog_add", "backlog_review", "diagram_from_video_url", "some_future_tool"}


def test_unknown_scope_is_refused():
    try:
        sc.load_principals('{"x": ["contnet.read"]}')
    except ValueError as e:
        assert "unknown scope" in str(e)
    else:
        raise AssertionError("typo in a scope name must fail loudly")


def test_unlisted_allowlisted_caller_stays_owner():
    p = sc.load_principals('{"user_team": ["content.read"]}')
    assert sc.scopes_for("user_linda", p) == {"owner"}
    assert sc.scopes_for("user_team", p) == {"content.read"}


def _tool(name):
    return types.SimpleNamespace(name=name)


def _run_list(mw):
    async def call_next(_ctx):
        return [_tool(n) for n in ALL]
    return [t.name for t in asyncio.run(mw.on_list_tools(None, call_next))]


def test_middleware_filters_by_sub():
    principals = sc.load_principals('{"user_team": ["content.read"], "pearl": ["content.read", "notes.write"]}')
    team_tok = types.SimpleNamespace(client_id="mcp-client-xyz", subject="user_team", scopes=[], claims={})
    mw = ScopeMiddleware(principals=principals, token_getter=lambda: team_tok)
    got = _run_list(mw)
    assert "search" in got and "ingest_note" not in got
    pearl_tok = types.SimpleNamespace(client_id="pearl", subject=None, scopes=[], claims={})
    mw = ScopeMiddleware(principals=principals, token_getter=lambda: pearl_tok)
    got = _run_list(mw)
    assert "ingest_note" in got and "backlog_add" not in got
    owner_tok = types.SimpleNamespace(client_id="c", subject="user_linda", scopes=[], claims={})
    mw = ScopeMiddleware(principals=principals, token_getter=lambda: owner_tok)
    assert _run_list(mw) == ALL
    mw = ScopeMiddleware(principals=principals, token_getter=lambda: None)   # stdio / anon
    assert _run_list(mw) == ALL


def test_middleware_refuses_out_of_scope_call():
    from fastmcp.exceptions import ToolError
    principals = sc.load_principals('{"user_team": ["content.read"]}')
    tok = types.SimpleNamespace(client_id="c", subject="user_team", scopes=[], claims={})
    mw = ScopeMiddleware(principals=principals, token_getter=lambda: tok)
    ctx = types.SimpleNamespace(message=types.SimpleNamespace(name="ingest_note"))

    async def call_next(_ctx):
        return "ran"
    try:
        asyncio.run(mw.on_call_tool(ctx, call_next))
    except ToolError:
        pass
    else:
        raise AssertionError("out-of-scope call must raise")
    ctx = types.SimpleNamespace(message=types.SimpleNamespace(name="search"))
    assert asyncio.run(mw.on_call_tool(ctx, call_next)) == "ran"


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)}/{len(fns)} passed")
