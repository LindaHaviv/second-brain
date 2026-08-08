"""Pure tests for the chat-export parser (oracle/agent/chat_export_core).

No I/O. Both WhatsApp dialects (iOS bracket, Android dash), multiline
messages, system-line dropping, and the last-state question the chaser asks.

  python tests/test_chat_export.py
"""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

import chat_export_core as core  # noqa: E402

IOS = """\
[8/1/26, 9:15:03 AM] Linda Haviv: Sending the footage tonight
[8/1/26, 9:16:44 AM] Editor Team: Got it!
We'll start the pre-cut tomorrow
[8/3/26, 2:05:12 PM] Editor Team: First draft is in the dashboard
"""

ANDROID = """\
8/1/26, 9:15 AM - Messages and calls are end-to-end encrypted.
8/1/26, 9:16 AM - Linda Haviv: Sending the footage tonight
8/2/26, 10:00 AM - Editor Team: <Media omitted>
8/3/26, 2:05 PM - Editor Team: First draft ready
"""


def test_ios_format_parses_all_messages():
    msgs = core.parse(IOS)
    assert len(msgs) == 3
    assert msgs[0]["sender"] == "Linda Haviv"
    assert msgs[0]["at"] == datetime.datetime(2026, 8, 1, 9, 15, 3)
    assert msgs[-1]["text"] == "First draft is in the dashboard"


def test_multiline_message_folds_into_previous():
    msgs = core.parse(IOS)
    assert "We'll start the pre-cut tomorrow" in msgs[1]["text"]


def test_android_format_and_system_lines_dropped():
    msgs = core.parse(ANDROID)
    assert len(msgs) == 3            # encryption notice dropped (no sender)
    assert msgs[0]["sender"] == "Linda Haviv"
    assert msgs[1]["text"] == "<Media omitted>"


def test_empty_export_is_none():
    assert core.last_state(core.parse(""), ["Linda"]) is None


def test_last_state_they_spoke_last():
    st = core.last_state(core.parse(IOS), ["Linda"])
    assert st["owner_last"] is False
    assert st["last_sender"] == "Editor Team"
    assert st["last_at"] == datetime.datetime(2026, 8, 3, 14, 5, 12)
    assert len(st["froms"]) == 3


def test_last_state_owner_spoke_last():
    text = IOS + "[8/4/26, 8:00:00 AM] Linda Haviv: Notes sent back!\n"
    st = core.last_state(core.parse(text), ["Linda"])
    assert st["owner_last"] is True


def test_owner_match_is_containment_case_insensitive():
    st = core.last_state(core.parse(IOS), ["linda haviv"])
    assert st["froms"][0] == "Linda Haviv"
    assert st["owner_last"] is False


def test_froms_feed_followup_verdicts():
    """The parser's froms + followup_core = the same verdicts email threads get."""
    import followup_core
    st = core.last_state(core.parse(IOS), ["Linda"])
    now = datetime.datetime(2026, 8, 8, 12, 0)
    # their message (Aug 3) has waited 5 days: she owes the reply
    assert followup_core.owed_reply(st["froms"], "Linda", st["last_at"], now) == "owes"


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
