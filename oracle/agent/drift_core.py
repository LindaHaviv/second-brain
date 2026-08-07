"""Doc-drift verdicts (PUBLIC, pure) — does the documentation match the machine?

A second brain accumulates scheduled jobs, agents, and loaders faster than its docs
accumulate rows describing them. The gap is quiet and it compounds: a job installed
on a Tuesday runs every day for a month with nothing in the system page saying it
exists, and a loader whose credential was never configured skips forever while the
docs still promise it runs.

Three comparisons, each an exact-match set difference (no fuzzy matching — a weekly
report that cries wolf stops being read):

  1. SCHEDULE   installed scheduler jobs   vs the clock table in your system doc
  2. REGISTRY   agent files on disk        vs the registry rows in your agents doc
  3. DORMANT    pipeline steps that need a credential vs the environment that is set

Everything here is pure: callers pass text and filenames, these functions return
findings. No filesystem, no clock, no network — so the verdicts are testable
(tests/test_drift.py) and the I/O stays in whatever agent calls them.

Nothing here reads or returns a credential VALUE. The dormant check compares
variable NAMES against the set of names that are set, which is all it needs.
"""
import re

# A markdown table row whose first bold cell names a thing: | **name** | ... |
_REGISTRY_ROW = re.compile(r"^\|\s*\*\*([A-Za-z_][A-Za-z0-9_-]*)\*\*", re.M)
# `token` — the way both docs name a job/agent inside a table cell
_BACKTICKED = re.compile(r"`([^`]+)`")
# (label, [argv], REQUIRED_ENV | None) — the shape of a pipeline step table
_STEP = re.compile(r"""\(\s*"([^"]+)"\s*,\s*\[.*?\]\s*,\s*(None|"[A-Z0-9_]+")\s*\)""", re.S)


def markdown_section(text, heading_starts_with):
    """The body of the first '## ...' section whose heading starts with the given
    string, up to the next '## '. Empty string when there is no such heading."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and line[3:].lstrip().startswith(heading_starts_with):
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


def documented_jobs(system_text, heading_starts_with):
    """Job names named in backticks inside the clock section of a system doc.

    Only the FIRST backticked token of a row is a job name; later ones are paths and
    flags (`scripts/sync.py`, `--refresh`), so anything containing '/', '.', or a
    leading '-' is dropped. A trailing '.*' glob (the section title's own
    `prefix.*` example) is dropped too."""
    out = set()
    for row in markdown_section(system_text, heading_starts_with).splitlines():
        if not row.lstrip().startswith("|"):
            continue
        for tok in _BACKTICKED.findall(row):
            tok = tok.strip()
            if not tok or tok.endswith(".*") or "/" in tok or "." in tok or tok.startswith("-"):
                continue
            out.add(tok)
            break          # first clean backticked token per row is the job
    return out


def installed_jobs(filenames, prefix, suffix=".plist"):
    """Job names from scheduler filenames: '<prefix><name><suffix>' -> '<name>'."""
    out = set()
    for f in filenames:
        name = f.rsplit("/", 1)[-1]
        if name.startswith(prefix) and name.endswith(suffix):
            stem = name[len(prefix):-len(suffix)] if suffix else name[len(prefix):]
            if stem:
                out.add(stem)
    return out


def registered_names(agents_text):
    """Names carrying a registry row (| **name** | ... |)."""
    return set(_REGISTRY_ROW.findall(agents_text))


def built_agents(filenames, ignore_prefixes=("_",), suffix=".py"):
    """Agent names from files on disk, minus private/helper modules."""
    out = set()
    for f in filenames:
        name = f.rsplit("/", 1)[-1]
        if not name.endswith(suffix):
            continue
        stem = name[: -len(suffix)]
        if not stem or any(stem.startswith(p) for p in ignore_prefixes):
            continue
        out.add(stem)
    return out


def pipeline_steps(source_text):
    """[(label, required_env or None)] parsed from a STEPS-style tuple table."""
    steps = []
    for label, env in _STEP.findall(source_text):
        steps.append((label, None if env == "None" else env.strip('"')))
    return steps


def dormant_steps(steps, env_names):
    """Steps whose required credential is absent -> they skip on every run.

    `env_names` is a set of variable NAMES that are set; values never enter here."""
    have = set(env_names)
    return [(label, var) for label, var in steps if var and var not in have]


def findings(installed, documented, built, registered, dormant,
             ignore_undocumented=(), ignore_unregistered=(), ignore_uninstalled=()):
    """The full verdict as a list of (kind, subject, message), sorted and stable.

    `ignore_*` let a caller whitelist known-and-accepted gaps so the report can stay
    silent about them without the check being switched off. `ignore_uninstalled` is
    for jobs that legitimately live on a DIFFERENT scheduler than the one being
    globbed — they belong in the clock table but will never appear as a job file."""
    out = []
    for job in sorted(installed - set(documented) - set(ignore_undocumented)):
        out.append(("schedule", job,
                    f"'{job}' is installed and running but has no row in the clock table"))
    for job in sorted(set(documented) - installed - set(ignore_uninstalled)):
        out.append(("schedule", job,
                    f"'{job}' is documented as scheduled but is not installed"))
    for agent in sorted(built - registered - set(ignore_unregistered)):
        out.append(("registry", agent,
                    f"'{agent}' exists but has no registry row (no row, no run)"))
    for label, var in sorted(dormant):
        out.append(("dormant", label,
                    f"'{label}' skips every run: {var} is not configured"))
    return out


def format_block(found, title="DOC DRIFT"):
    """Report text. Propose-only: it never says a gap is an error, only that it is a
    gap, because closing it can mean editing the doc OR removing the job."""
    if not found:
        return f"{title}: none — schedule, registry, and pipeline all match their docs."
    lines = [f"{title} ({len(found)} to reconcile — propose-only, nothing changed):"]
    for kind, _subject, message in found:
        lines.append(f"  [{kind}] {message}")
    return "\n".join(lines)
