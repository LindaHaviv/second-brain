"""Pure tests for the review-hub sync verdict (oracle/agent/gdrive_review).

Two surfaces editing one document is how drift starts. The verdict is the whole
safety property: newer-wins only when there is no history, change-since-last-sync
when there is, and a genuine two-sided edit is a CONFLICT that overwrites nothing.

  python tests/test_drive_sync.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

from gdrive_review import sync_verdict  # noqa: E402

T = 1000.0   # a base timestamp; +/- seconds from here


def test_missing_both_sides():
    assert sync_verdict(None, None) == "missing"


def test_one_side_missing_moves_the_other_way():
    assert sync_verdict(None, T) == "pull"      # only Drive has it
    assert sync_verdict(T, None) == "push"      # only local has it


def test_no_history_means_newer_wins():
    assert sync_verdict(T + 60, T) == "push"
    assert sync_verdict(T, T + 60) == "pull"
    assert sync_verdict(T, T + 1) == "skip"     # inside clock-skew tolerance


def test_with_history_only_the_changed_side_moves():
    last = T
    assert sync_verdict(T + 60, T, last_sync=last) == "push"    # local edited
    assert sync_verdict(T, T + 60, last_sync=last) == "pull"    # Doc edited
    assert sync_verdict(T, T, last_sync=last) == "skip"         # neither


def test_both_changed_is_a_conflict_and_never_a_silent_overwrite():
    """The case that matters: she edits the Doc on her phone while a shift
    rewrites the file at home. Nothing is overwritten; a human decides."""
    assert sync_verdict(T + 60, T + 90, last_sync=T) == "conflict"
    assert sync_verdict(T + 90, T + 60, last_sync=T) == "conflict"


def test_tolerance_is_tunable():
    assert sync_verdict(T + 5, T, tolerance=10) == "skip"
    assert sync_verdict(T + 5, T, tolerance=1) == "push"


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
