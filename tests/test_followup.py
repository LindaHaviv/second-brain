"""Pure tests for the follow-up verdict engine (oracle/agent/followup_core).

No network, no clock — every case fixes `now`. This is the eval that earns the
follow-up chaser its keep: the verdict IS the value (the I/O around it is thin),
so the verdict is what's pinned. The self-only rule has its own regression case
because it was learned the hard way: the first live run flagged 21 threads and
20 were the owner's own daily-brief mails matching a brand term.

  python tests/test_followup.py
"""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

import followup_core as core  # noqa: E402

ME = "linda@example.com"
NOW = datetime.datetime(2026, 7, 17, 12, 0)


def _days_ago(n):
    return NOW - datetime.timedelta(days=n)


# ---- sender matching ----------------------------------------------------------------

def test_sender_matches_display_name_form():
    """Real From headers wrap the address: 'Linda H <linda@example.com>'."""
    assert core.sender_is(ME, "Linda H <linda@example.com>")
    assert core.sender_is(ME, "LINDA@EXAMPLE.COM")
    assert not core.sender_is(ME, "Brand Person <person@brand.com>")
    assert not core.sender_is(ME, "")
    assert not core.sender_is("", "anyone@anywhere.com")   # no owner -> never a match


# ---- the verdict rules, one case each -----------------------------------------------

def test_empty_thread():
    assert core.thread_verdict([], ME, _days_ago(10), NOW) == "empty"


def test_self_only_thread_is_never_a_chase():
    """REGRESSION (2026-07-17): the owner's own newsletter/digest mails mention brand
    terms, the owner is the only sender, and 'owner sent last' is trivially true —
    without this rule 20 of 21 first-run flags were false positives."""
    froms = ["Linda H <linda@example.com>", "Linda H <linda@example.com>"]
    assert core.thread_verdict(froms, ME, _days_ago(30), NOW) == "self-only"


def test_counterparty_spoke_last_is_answered():
    """They replied — the ball is in the owner's court, not a chase."""
    froms = ["Linda H <linda@example.com>", "Brand <person@brand.com>"]
    assert core.thread_verdict(froms, ME, _days_ago(30), NOW) == "answered"


def test_owner_last_but_recent_is_too_recent():
    froms = ["Brand <person@brand.com>", "Linda H <linda@example.com>"]
    assert core.thread_verdict(froms, ME, _days_ago(2), NOW) == "too-recent"


def test_owner_last_and_stale_is_a_chase():
    froms = ["Brand <person@brand.com>", "Linda H <linda@example.com>"]
    assert core.thread_verdict(froms, ME, _days_ago(9), NOW) == "chase"


# ---- boundaries and knobs -----------------------------------------------------------

def test_quiet_threshold_is_inclusive_at_the_boundary():
    """Exactly quiet_days old -> chase (the wait is over, not still running)."""
    froms = ["Brand <person@brand.com>", "Linda H <linda@example.com>"]
    assert core.thread_verdict(froms, ME, _days_ago(core.QUIET_DAYS), NOW) == "chase"
    just_under = NOW - datetime.timedelta(days=core.QUIET_DAYS) + datetime.timedelta(minutes=1)
    assert core.thread_verdict(froms, ME, just_under, NOW) == "too-recent"


def test_quiet_days_is_tunable():
    froms = ["Brand <person@brand.com>", "Linda H <linda@example.com>"]
    assert core.thread_verdict(froms, ME, _days_ago(5), NOW, quiet_days=7) == "too-recent"
    assert core.thread_verdict(froms, ME, _days_ago(5), NOW, quiet_days=3) == "chase"


def test_long_thread_only_last_sender_decides_the_court():
    """A busy thread: many turns, owner's message last and stale -> chase, regardless
    of who spoke in the middle."""
    froms = ["Linda H <linda@example.com>", "Brand <a@brand.com>",
             "Agency <b@agency.com>", "Linda H <linda@example.com>"]
    assert core.thread_verdict(froms, ME, _days_ago(6), NOW) == "chase"


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
