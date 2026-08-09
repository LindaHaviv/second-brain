"""Pure tests for the chat-webhook gate (oracle/agent/chat_gate) and the
tool-schema translation (oracle/agent/chat_agent.to_anthropic_tools).

The gate is the security boundary of an unauthenticated public route, so every
verdict is pinned: wrong secret is forbidden, everything inauthentic-but-valid
is silently ignored, and only allowlisted text messages pass.

  python tests/test_chat_gate.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

import chat_gate  # noqa: E402
from chat_agent import to_anthropic_tools  # noqa: E402

SECRET = "s3cret"
ALLOWED = ["12345"]


def _update(text="hi", chat=12345, extra=None):
    u = {"update_id": 7,
         "message": {"message_id": 9, "text": text, "chat": {"id": chat},
                     "from": {"first_name": "Linda"}}}
    if extra:
        u.update(extra)
    return u


# ---- the gate ------------------------------------------------------------------------

def test_wrong_or_missing_secret_is_forbidden():
    assert chat_gate.gate("nope", SECRET, _update(), ALLOWED)[0] == "forbidden"
    assert chat_gate.gate(None, SECRET, _update(), ALLOWED)[0] == "forbidden"


def test_unconfigured_secret_fails_closed():
    """No expected secret configured -> NOTHING passes, ever."""
    assert chat_gate.gate("", "", _update(), ALLOWED)[0] == "forbidden"
    assert chat_gate.gate(None, None, _update(), ALLOWED)[0] == "forbidden"


def test_non_message_updates_are_ignored():
    assert chat_gate.gate(SECRET, SECRET, {"update_id": 1}, ALLOWED)[0] == "ignore"
    assert chat_gate.gate(SECRET, SECRET,
                          {"edited_message": {"text": "x"}}, ALLOWED)[0] == "ignore"
    assert chat_gate.gate(SECRET, SECRET, "garbage", ALLOWED)[0] == "ignore"


def test_textless_messages_are_ignored():
    u = {"update_id": 2, "message": {"message_id": 3, "chat": {"id": 12345}}}
    assert chat_gate.gate(SECRET, SECRET, u, ALLOWED)[0] == "ignore"


def test_stranger_chat_is_silently_ignored():
    assert chat_gate.gate(SECRET, SECRET, _update(chat=666), ALLOWED)[0] == "ignore"
    assert chat_gate.gate(SECRET, SECRET, _update(), [])[0] == "ignore"


def test_allowed_text_message_passes_with_fields():
    verdict, info = chat_gate.gate(SECRET, SECRET, _update("  hello  "), ALLOWED)
    assert verdict == "ok"
    assert info == {"chat_id": "12345", "text": "hello", "voice_file_id": None,
                    "message_id": 9, "update_id": 7, "sender": "Linda"}


def test_voice_message_passes_with_file_id():
    """A voice note from an allowed chat is admitted (empty text, file id set) —
    the caller transcribes; a voice note from a stranger still drops silently."""
    u = {"update_id": 8, "message": {"message_id": 10, "chat": {"id": 12345},
                                     "from": {"first_name": "Linda"},
                                     "voice": {"file_id": "VF123", "duration": 4}}}
    verdict, info = chat_gate.gate(SECRET, SECRET, u, ALLOWED)
    assert verdict == "ok"
    assert info["voice_file_id"] == "VF123" and info["text"] == ""
    u["message"]["chat"]["id"] = 666
    assert chat_gate.gate(SECRET, SECRET, u, ALLOWED)[0] == "ignore"


def test_allowlist_accepts_ints_and_strings():
    assert chat_gate.gate(SECRET, SECRET, _update(), [12345])[0] == "ok"


# ---- tool-schema translation ---------------------------------------------------------

def test_tools_filtered_and_ordered_by_allowlist():
    tools = [{"name": "search", "description": "find things",
              "inputSchema": {"type": "object", "properties": {}}},
             {"name": "nuke", "description": "dangerous", "inputSchema": {}},
             {"name": "wiki", "description": "read a page", "inputSchema": {}}]
    out = to_anthropic_tools(tools, ["wiki", "search"])
    assert [t["name"] for t in out] == ["wiki", "search"]   # allowlist order
    assert all(t["name"] != "nuke" for t in out)            # unlisted never leaks
    assert out[1]["input_schema"] == {"type": "object", "properties": {}}


def test_missing_schema_degrades_to_empty_object():
    out = to_anthropic_tools([{"name": "t", "description": "", "inputSchema": None}],
                             ["t"])
    assert out[0]["input_schema"] == {"type": "object"}


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
            msg = (str(e).splitlines() or [e.__class__.__name__])[0] or e.__class__.__name__
            print(f"  FAIL  {n}: {msg}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
