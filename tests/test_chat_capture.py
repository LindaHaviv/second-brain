"""Pure tests for the chat-capture boundary (oracle/agent/chat_capture).

This is the gate that makes an automatic sweep safer than a raw export: an
export keeps the medical question and the furniture shopping, a sweep must not.
The asymmetry is deliberate — a wrong skip costs one unsaved chat, a wrong keep
puts private life into a searchable index — so the tests pin the skips hardest.

  python tests/test_chat_capture.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

import chat_capture as cc  # noqa: E402


def deny(text):
    """Stand-in for the deployment's private-by-policy check."""
    low = (text or "").lower()
    for pat in ("exclusivity", "usage rights", "invoice", "rate card"):
        if pat in low:
            return pat
    return None


# ---- the keep set ---------------------------------------------------------------------

def test_working_record_is_kept():
    v, why = cc.should_capture("GitLab collaboration script concept",
                               "we decided the hook opens on the clone story", deny)
    assert v == cc.KEEP and why == "working record"


def test_technical_and_system_chats_are_kept():
    assert cc.should_capture("Second brain database research",
                             "comparing vector index options", deny)[0] == cc.KEEP
    assert cc.should_capture("Instagram reels performance analysis",
                             "which hooks retained past 3 seconds", deny)[0] == cc.KEEP


# ---- private by policy: skipped, never merely quarantined ------------------------------

def test_deal_terms_are_skipped_outright():
    """Business/deal data must not reach the cloud brain at all — quarantining
    still stores it, so a sweep skips."""
    v, why = cc.should_capture("Contract review",
                               "they want 1 month exclusivity on the reel", deny)
    assert v == cc.SKIP
    assert "deny-list" in why


def test_deny_check_runs_before_anything_else():
    """A chat that is both personal-sounding and policy-private reports policy."""
    v, why = cc.should_capture("Invoice for the doctor visit", "usage rights too", deny)
    assert v == cc.SKIP and "deny-list" in why


# ---- personal life ---------------------------------------------------------------------

def test_health_home_and_errands_are_skipped():
    for title, body in [("Swollen eyelid with pinching sensation", "should I see a doctor"),
                        ("King bed rug sizing guide", "8x10 under the frame?"),
                        ("Toy Story 5 tickets for 5 people", "showtimes tonight"),
                        ("Gas spring resistance on lift up bed", "replacing the struts")]:
        v, why = cc.should_capture(title, body, deny)
        assert v == cc.SKIP, (title, why)
        assert "personal life" in why


def test_empty_is_skipped():
    assert cc.should_capture("", "", deny)[0] == cc.SKIP


# ---- the rescue: personal wording, real work -------------------------------------------

def test_work_context_rescues_personal_wording():
    """A trip that is also a shoot is working record; the packing words alone
    must not throw it away."""
    v, why = cc.should_capture("Family Eurotrip planning",
                               "what to pack, and I am filming the France video there",
                               deny, work_terms=("filming", "video", "brand"))
    assert v == cc.KEEP
    assert "work context" in why


def test_raw_pass_skips_the_policy_check_by_design():
    """The two-stage rule, learned from the first real dry run: an hour-long
    working session mentions a dollar sign somewhere, and the raw transcript is
    never what gets stored. Screening raw text against the policy list threw
    away almost every genuine working record. So raw = personal gate only;
    the policy gate runs on the distilled note, which IS the stored copy."""
    long_session = ("we redesigned the ranking engine, and somewhere in an hour "
                    "of conversation the words usage rights appeared once")
    assert cc.should_capture("Ranking engine rebuild", long_session, deny,
                             apply_deny=False)[0] == cc.KEEP
    assert cc.should_capture("Ranking engine rebuild", long_session, deny,
                             apply_deny=True)[0] == cc.SKIP


def test_rescue_does_not_override_policy():
    """Work vocabulary never rescues something the deny-list caught."""
    v, why = cc.should_capture("Brand trip", "filming, and the usage rights are 3 months",
                               deny, work_terms=("filming",))
    assert v == cc.SKIP and "deny-list" in why


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
