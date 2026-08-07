"""Pure tests for the doc-drift engine (oracle/agent/drift_core).

No filesystem, no clock — every case passes text in. This is the eval that earns the
drift check its keep: the verdict IS the value, so the verdict is what's pinned.

Two cases exist because they were the real findings that motivated the check: a job
installed by one session while another was mid-flight (running for a month with no
row in the system doc), and a pipeline step whose credential was never configured
(built, shipped, and silently skipping on every run since).

  python tests/test_drift.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

import drift_core as core  # noqa: E402

SYSTEM_DOC = """# System

## The daily/weekly clock (launchd, `com.example.brain.*`)

| When | Job | What it does |
|---|---|---|
| 9:00 daily | `sync` -> scripts/sync.py | everything |
| 9:10 Monday | `freshness` -> agents/freshness_check.py | the one-pager |
| 12:15 daily | `watchdog` -> `scripts/watchdog.py` | the alarm |

Logs live somewhere.

## Something else

| 9:99 | `notajob` | this table is in another section |
"""

AGENTS_DOC = """# Registry

| **sync** | daily | ... |
| **freshness_check** | Monday | ... |
| **watchdog** | daily | ... |
"""

SYNC_SRC = '''
STEPS = [
    ("IG token refresh", [str(ROOT / "scripts" / "instagram_token.py"),
                          "--refresh", "--auto"],                   "IG_ACCESS_TOKEN"),
    ("Notion",       [str(ROOT / "scripts" / "notion.py")],         "NOTION_TOKEN"),
    ("Reconcile",    [str(ROOT / "scripts" / "reconcile.py")],      "NOTION_CONTENT_TRACKER"),
    ("Wiki refresh", [str(ROOT / "wiki.py"), "--refresh"],          None),
]
'''


# ---- section + job parsing ----------------------------------------------------------

def test_section_stops_at_the_next_heading():
    body = core.markdown_section(SYSTEM_DOC, "The daily/weekly clock")
    assert "sync" in body
    assert "notajob" not in body, "leaked into the next section"


def test_missing_section_is_empty_not_an_error():
    assert core.markdown_section(SYSTEM_DOC, "No Such Heading") == ""


def test_documented_jobs_takes_only_the_job_token():
    """Paths and flags are backticked too; only the first clean token is the job."""
    jobs = core.documented_jobs(SYSTEM_DOC, "The daily/weekly clock")
    assert jobs == {"sync", "freshness", "watchdog"}, jobs


def test_documented_jobs_ignores_the_heading_glob():
    """The section title carries `com.example.brain.*` — not a job."""
    assert not any(j.endswith(".*") for j in
                   core.documented_jobs(SYSTEM_DOC, "The daily/weekly clock"))


def test_installed_jobs_strips_prefix_and_suffix():
    files = ["/x/com.example.brain.sync.plist",
             "/x/com.example.brain.pipeline.plist",
             "/x/com.other.thing.plist"]
    assert core.installed_jobs(files, "com.example.brain.") == {"sync", "pipeline"}


def test_built_agents_skips_private_modules():
    files = ["a/_bootstrap.py", "a/_template.py", "a/backlog.py", "a/notes.txt"]
    assert core.built_agents(files) == {"backlog"}


def test_registered_names_reads_bold_rows():
    assert core.registered_names(AGENTS_DOC) == {"sync", "freshness_check", "watchdog"}


# ---- pipeline steps -----------------------------------------------------------------

def test_pipeline_steps_parses_multiline_entries():
    steps = core.pipeline_steps(SYNC_SRC)
    assert ("IG token refresh", "IG_ACCESS_TOKEN") in steps, steps
    assert ("Wiki refresh", None) in steps
    assert len(steps) == 4


def test_dormant_steps_flags_only_unset_credentials():
    steps = core.pipeline_steps(SYNC_SRC)
    dormant = core.dormant_steps(steps, {"IG_ACCESS_TOKEN", "NOTION_TOKEN"})
    assert dormant == [("Reconcile", "NOTION_CONTENT_TRACKER")], dormant


def test_dormant_never_flags_a_step_that_needs_nothing():
    assert core.dormant_steps([("Wiki refresh", None)], set()) == []


# ---- the verdict --------------------------------------------------------------------

def test_clean_system_reports_nothing():
    found = core.findings(installed={"sync"}, documented={"sync"}, built={"sync"},
                          registered={"sync"}, dormant=[])
    assert found == []
    assert "none" in core.format_block(found)


def test_installed_but_undocumented_job_is_caught():
    """The real case: a sibling session installed 'pipeline' at 12:00."""
    found = core.findings(installed={"sync", "pipeline"}, documented={"sync"},
                          built=set(), registered=set(), dormant=[])
    assert len(found) == 1
    kind, subject, msg = found[0]
    assert (kind, subject) == ("schedule", "pipeline")
    assert "no row in the clock table" in msg


def test_documented_but_not_installed_job_is_caught():
    found = core.findings(installed=set(), documented={"digest"},
                          built=set(), registered=set(), dormant=[])
    assert found[0][0] == "schedule"
    assert "not installed" in found[0][2]


def test_unregistered_agent_is_caught():
    found = core.findings(installed=set(), documented=set(),
                          built={"athena"}, registered=set(), dormant=[])
    assert found[0][:2] == ("registry", "athena")


def test_dormant_step_reaches_the_report_by_name_only():
    """A credential NAME may appear; a value must never be involved."""
    found = core.findings(installed=set(), documented=set(), built=set(),
                          registered=set(), dormant=[("Reconcile", "NOTION_CONTENT_TRACKER")])
    assert found[0][0] == "dormant"
    assert "NOTION_CONTENT_TRACKER" in found[0][2]
    assert "skips every run" in found[0][2]


def test_whitelist_silences_a_known_gap_without_disabling_the_check():
    found = core.findings(installed={"sync", "pipeline"}, documented={"sync"},
                          built={"athena"}, registered=set(), dormant=[],
                          ignore_undocumented=("pipeline",),
                          ignore_unregistered=("athena",))
    assert found == []


def test_job_on_another_scheduler_is_not_reported_missing():
    """A monthly task run by a different scheduler belongs in the clock table but
    will never show up as a job file — that is documentation, not drift."""
    found = core.findings(installed=set(), documented={"context-maintenance"},
                          built=set(), registered=set(), dormant=[],
                          ignore_uninstalled=("context-maintenance",))
    assert found == []


def test_findings_are_stable_and_sorted():
    a = core.findings({"b", "a"}, set(), set(), set(), [])
    assert a == core.findings({"a", "b"}, set(), set(), set(), [])
    assert [f[1] for f in a] == ["a", "b"]


def test_format_block_lists_every_finding():
    found = core.findings(installed={"pipeline"}, documented=set(),
                          built={"athena"}, registered=set(),
                          dormant=[("Reconcile", "NOTION_CONTENT_TRACKER")])
    text = core.format_block(found)
    assert "3 to reconcile" in text
    assert "propose-only" in text
    for token in ("pipeline", "athena", "Reconcile"):
        assert token in text


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
