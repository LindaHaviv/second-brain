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


# ---- the mirror verdict: does the OWNER owe a reply? --------------------------------

def test_owed_empty_and_self_only():
    assert core.owed_reply([], ME, _days_ago(10), NOW) == "empty"
    froms = ["Linda H <linda@example.com>", "Linda H <linda@example.com>"]
    assert core.owed_reply(froms, ME, _days_ago(30), NOW) == "self-only"


def test_owner_spoke_last_owes_nothing():
    """Her message last -> the other direction's problem (thread_verdict), never 'owes'."""
    froms = ["Editor <cut@editor.com>", "Linda H <linda@example.com>"]
    assert core.owed_reply(froms, ME, _days_ago(30), NOW) == "waiting-on-them"


def test_their_recent_message_is_fresh_not_owed():
    froms = ["Linda H <linda@example.com>", "Editor <cut@editor.com>"]
    assert core.owed_reply(froms, ME, _days_ago(1), NOW) == "fresh"


def test_their_stale_message_is_owed():
    """They sent the edit back days ago and nothing went out — the owner owes them."""
    froms = ["Linda H <linda@example.com>", "Editor <cut@editor.com>"]
    assert core.owed_reply(froms, ME, _days_ago(5), NOW) == "owes"


def test_owed_boundary_is_inclusive_and_tunable():
    froms = ["Linda H <linda@example.com>", "Editor <cut@editor.com>"]
    assert core.owed_reply(froms, ME, _days_ago(core.OWE_DAYS), NOW) == "owes"
    just_under = NOW - datetime.timedelta(days=core.OWE_DAYS) + datetime.timedelta(minutes=1)
    assert core.owed_reply(froms, ME, just_under, NOW) == "fresh"
    assert core.owed_reply(froms, ME, _days_ago(4), NOW, owe_days=7) == "fresh"
    assert core.owed_reply(froms, ME, _days_ago(4), NOW, owe_days=2) == "owes"


# ---- work calendars ------------------------------------------------------------------

MON_FRI = frozenset(range(5))


def test_weekend_does_not_count_against_a_mon_fri_team():
    """Friday-evening message checked Monday morning: zero WHOLE working days
    have passed for a mon-fri team (Monday isn't over), while the all-week
    count sees the weekend. NOW elsewhere is Fri 2026-07-17."""
    fri = datetime.datetime(2026, 7, 10, 17, 0)
    mon = datetime.datetime(2026, 7, 13, 9, 0)
    assert core.working_days(fri, mon, MON_FRI) == 0          # Mon isn't over yet
    assert core.working_days(fri, mon, frozenset(range(7))) == 2  # Sat + Sun elapsed
    froms = ["Brand <person@brand.com>", "Linda H <linda@example.com>"]
    # her Friday message by the NEXT Friday: chase either way (a full week passed,
    # 4 whole workdays of it), but a midweek message stays too-recent for a
    # mon-fri team while the calendar count would already flag it
    assert core.thread_verdict(froms, ME, fri, NOW, quiet_days=3) == "chase"
    assert core.thread_verdict(froms, ME, fri, NOW, quiet_days=3,
                               workdays=MON_FRI) == "chase"   # Mon..Thu = 4 >= 3
    wed = datetime.datetime(2026, 7, 15, 17, 0)
    assert core.thread_verdict(froms, ME, wed, NOW, quiet_days=2) == "too-recent"
    assert core.thread_verdict(froms, ME, wed, NOW, quiet_days=1,
                               workdays=MON_FRI) == "chase"   # Thu elapsed = 1 >= 1
    assert core.thread_verdict(froms, ME, wed, NOW, quiet_days=3,
                               workdays=MON_FRI) == "too-recent"  # only Thu = 1 < 3


def test_owed_reply_respects_workdays_too():
    """Their Friday-evening message, checked Monday morning: an all-week team is
    owed (3 calendar days), a mon-fri sender has waited one working day."""
    froms = ["Linda H <linda@example.com>", "Editor <cut@editor.com>"]
    fri = datetime.datetime(2026, 7, 10, 18, 0)
    mon = datetime.datetime(2026, 7, 13, 9, 0)
    assert core.owed_reply(froms, ME, fri, mon, owe_days=2) == "owes"
    assert core.owed_reply(froms, ME, fri, mon, owe_days=2,
                           workdays=MON_FRI) == "fresh"


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
