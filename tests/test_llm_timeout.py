"""A hung model call must not hold a sync open indefinitely, and a growth-step failure
must not stop the wiki from recording the refreshes it already committed."""
import importlib
import os
import sys
import types
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "oracle" / "agent"))


def test_llm_timeout_is_bounded_and_env_tunable(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "42")
    import llm
    importlib.reload(llm)
    assert llm.LLM_TIMEOUT == 42.0
    monkeypatch.delenv("LLM_TIMEOUT")
    importlib.reload(llm)
    assert llm.LLM_TIMEOUT == 600.0


def test_anthropic_client_gets_timeout_and_single_retry(monkeypatch):
    import llm
    importlib.reload(llm)
    seen = {}

    class _Msgs:
        def create(self, **kw):
            raise RuntimeError("stop here")

    class _Client:
        def __init__(self, **kw):
            seen.update(kw)
            self.messages = _Msgs()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_Client))
    monkeypatch.setattr(llm, "PROVIDER", "anthropic")
    try:
        llm._call("s", "p", {"type": "object"}, 10, "m")
    except RuntimeError:
        pass
    assert seen == {"timeout": llm.LLM_TIMEOUT, "max_retries": 1}


def test_wiki_growth_failure_does_not_block_bookkeeping():
    src = (pathlib.Path(__file__).resolve().parent.parent / "oracle" / "agent" / "wiki.py").read_text()
    growth = src[src.index("# growth:"):src.index("_set_hwm(cur, cur_max)")]
    assert "try:" in growth and "except Exception" in growth, "growth step must be fenced"
    assert "growth step skipped" in growth


def test_consolidate_revives_a_dropped_db_session(monkeypatch):
    import semantic_memory as sm

    class Dead:
        def ping(self):
            raise RuntimeError("DPY-1001: not connected to database")

    class Fresh:
        def ping(self):
            return None

    fresh = Fresh()
    monkeypatch.setitem(sys.modules, "db", types.SimpleNamespace(open_connection=lambda: fresh))
    assert sm._alive(Dead()) is fresh
    assert sm._alive(fresh) is fresh
