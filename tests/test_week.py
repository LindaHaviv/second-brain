"""Pure tests for the said-vs-did engine (oracle/agent/week_core).

No I/O, no clock. The weekly review's value IS the comparison — which promises
moved, where the week actually went — so the comparison is what's pinned. The
matcher cases mirror the reconcile matcher's hard-won lessons: planned titles
and posted captions share nouns, not phrasing, and a weak brand-name-only
overlap must be refused rather than flattering the week.

  python tests/test_week.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

import week_core as core  # noqa: E402


# ---- the matcher --------------------------------------------------------------------

def test_normalize_strips_punctuation_and_case():
    assert core.normalize("The ML Pipeline, End to End!") == "the ml pipeline end to end"
    assert core.normalize("") == ""
    assert core.normalize(None) == ""


def test_exact_title_matches():
    assert core.score("Film AI infra video", "Film AI infra video") == 1.0


def test_containment_matches_planned_title_inside_caption():
    s = core.score("Neoclouds explainer",
                   "Posted: neoclouds explainer — training, inference, RL")
    assert s >= 0.85


def test_noun_overlap_matches_rephrased_title():
    """Planned title vs posted caption: same nouns, different phrasing."""
    s = core.score("The Friction That Built Datadog together with DevOps",
                   "Datadog was born out of the friction between Dev & Ops")
    assert s > core.MATCH_THRESHOLD


def test_weak_single_word_overlap_is_refused():
    """Sharing one brand word must NOT count as the promise having moved."""
    s = core.score("Pitch Databricks two more paid videos",
                   "Databricks certification carousel")
    assert s <= core.MATCH_THRESHOLD


# ---- the comparison -----------------------------------------------------------------

def test_done_and_untouched_verdicts():
    out = core.compare(["Film AI infra video", "Second brain guide"],
                       ["Film AI infra video"])
    assert out["verdicts"][0]["status"] == "done"
    assert out["verdicts"][0]["evidence"] == "Film AI infra video"
    assert out["verdicts"][1]["status"] == "untouched"
    assert out["verdicts"][1]["evidence"] is None
    assert out["unplanned"] == []


def test_unplanned_work_is_surfaced_not_hidden():
    out = core.compare(["Second brain guide"],
                       ["Emergency brand video", "Second brain guide outline"])
    assert out["verdicts"][0]["status"] == "done"
    assert out["unplanned"] == ["Emergency brand video"]


def test_empty_said_means_everything_is_unplanned():
    out = core.compare([], ["Shipped a reel"])
    assert out["verdicts"] == []
    assert out["unplanned"] == ["Shipped a reel"]


def test_empty_did_means_everything_untouched():
    out = core.compare(["Film AI infra video"], [])
    assert out["verdicts"][0]["status"] == "untouched"
    assert out["unplanned"] == []


def test_verdicts_keep_said_order():
    out = core.compare(["B item", "A item"], [])
    assert [v["said"] for v in out["verdicts"]] == ["B item", "A item"]


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
