"""scripts/claude_code.py must only touch sessions that changed (pure planner)."""
import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))


def _mod():
    return importlib.import_module("claude_code")


def test_plan_unchanged_new_changed_removed():
    cc = _mod()
    prev = {"claude-code://a/s1": {"size": 10, "mtime": 1.0, "post_id": 101},
            "claude-code://a/s2": {"size": 20, "mtime": 2.0, "post_id": 102},
            "claude-code://b/gone": {"size": 5, "mtime": 3.0, "post_id": 103}}
    cur = {"claude-code://a/s1": (10, 1.0),        # unchanged
           "claude-code://a/s2": (25, 2.5),        # grew -> reload, drop old post
           "claude-code://c/new": (7, 9.0)}        # brand new
    load, delete, unchanged = cc.plan(cur, prev)
    assert unchanged == 1
    assert set(load) == {"claude-code://a/s2", "claude-code://c/new"}
    assert sorted(delete) == [102, 103]


def test_plan_first_run_loads_everything():
    cc = _mod()
    cur = {"claude-code://a/s1": (10, 1.0), "claude-code://a/s2": (20, 2.0)}
    load, delete, unchanged = cc.plan(cur, {})
    assert set(load) == set(cur) and delete == [] and unchanged == 0


def test_session_url_is_stable_and_project_scoped():
    cc = _mod()
    p = pathlib.Path("/Users/x/.claude/projects/-Users-x-repo/abc123.jsonl")
    assert cc.session_url(p) == "claude-code://-Users-x-repo/abc123"
