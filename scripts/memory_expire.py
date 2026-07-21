"""Raw-log rotation — the "consolidate, then expire" half of memory forgetting.

  ./.venv/bin/python scripts/memory_expire.py            # report: what WOULD expire
  ./.venv/bin/python scripts/memory_expire.py --apply    # rotate (the scheduled mode)

The raw memory layers (`agent_memory` run log, `conversations` dialogue turns) grow
without bound, and a noisy store degrades recall long before it fills a disk. But the
DISTILLED layer already preserves what those rows taught: consolidation runs daily and
folds episodic runs + conversation questions into semantic facts. So rows older than
the retention window are log entries whose lesson has been extracted many times over —
this rotates them out, exactly like log rotation.

Scope and guards (what keeps this safe to schedule):
  - Touches ONLY the raw stores. Distilled memories (semantic_memory, the package's
    memory store) are curated via scripts/memory_review.py — report-only, human retires.
  - Refuses to --apply unless a consolidation snapshot EXISTS and is FRESH (ran within
    the last few days). Existence protects a fresh brain; freshness protects against the
    quiet failure mode: the sync continues past a failed Consolidate step, and without
    this check a silently-broken consolidator (expired LLM key, say) would let months of
    undistilled experience rotate away while old facts still satisfied an exists-only
    guard. The sync runs consolidation immediately before this step, so scheduled
    rotation normally always follows a same-run distill.
  - Retention is MEMORY_RETENTION_DAYS (default 90; 0 disables). At a daily consolidation
    cadence, a row must survive ~90 distills before it can expire.
  - Rows tagged private ('business') expire too — retention doubles as a privacy floor:
    raw rows the deny-list quarantined do not live forever either.
  - Before deleting, CONTENT-scope rows are appended to a local tombstone
    (exports/memory_tombstones.jsonl, gitignored) — cheap insurance against the
    lossiness of a ~40-fact distilled cap. Business-tagged rows are deliberately NOT
    archived: the privacy floor means quarantined rows expire for real.
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))

from dotenv import load_dotenv
load_dotenv(ROOT / "oracle" / ".env")   # standalone-safe (launchd/any cwd), like consolidate.py

import oracledb  # noqa: E402

import db        # noqa: E402

RAW_TABLES = ("agent_memory", "conversations")   # raw layers ONLY — never the distilled stores
DISTILL_MAX_AGE_DAYS = 7      # a consolidation older than this no longer licenses rotation
TOMBSTONE = ROOT / "exports" / "memory_tombstones.jsonl"


def retention_days(env=os.environ):
    """Pure + unit-tested: the configured window, 0/invalid meaning 'disabled'."""
    try:
        return max(0, int(env.get("MEMORY_RETENTION_DAYS", "90")))
    except ValueError:
        return 0


def license_verdict(distill_age_days, max_age=DISTILL_MAX_AGE_DAYS):
    """Pure + unit-tested: why rotation is NOT licensed ('' = licensed). Rotation is
    licensed only by a consolidation snapshot that exists AND ran recently — the
    consolidator is what preserves the expiring rows' lessons, so 'it ran once, long
    ago' must not license deleting what it never saw."""
    if distill_age_days is None:
        return "no consolidation snapshot yet"
    if distill_age_days > max_age:
        return (f"last consolidation was {distill_age_days}d ago (max {max_age}d) — "
                "fix the Consolidate step before anything rotates")
    return ""


def rotation_license(conn):
    """The live check behind license_verdict: age in days of the newest consolidation row."""
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT TRUNC(SYSDATE - CAST(MAX(created_at) AS DATE)) "
                        "FROM semantic_memory WHERE source = 'consolidation'")
            age = cur.fetchone()[0]
        except oracledb.DatabaseError:
            return "semantic_memory store not present"
    return license_verdict(None if age is None else int(age))


def archive_expiring(conn, days, path=TOMBSTONE):
    """Append the CONTENT-scope rows about to expire to a local JSONL tombstone, so
    rotation is reversible-by-hand even though the distilled layer is a capped summary.
    Business-tagged rows are deliberately excluded — archiving them would quietly undo
    the privacy floor (quarantined rows must actually die). Returns rows archived."""
    import json
    rows = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT run_id, task, action, tool, outcome, detail, "
            "TO_CHAR(created_at,'YYYY-MM-DD HH24:MI') FROM agent_memory "
            "WHERE created_at < SYSTIMESTAMP - NUMTODSINTERVAL(:d, 'DAY') "
            "AND NVL(visibility,'content') = 'content'", d=days)
        for run_id, task, action, tool, outcome, detail, when in cur.fetchall():
            rows.append({"table": "agent_memory", "when": when, "run_id": run_id,
                         "task": task, "action": action, "tool": tool,
                         "outcome": outcome, "detail": detail})
        cur.execute(
            "SELECT session_id, seq, role, content, "
            "TO_CHAR(created_at,'YYYY-MM-DD HH24:MI') FROM conversations "
            "WHERE created_at < SYSTIMESTAMP - NUMTODSINTERVAL(:d, 'DAY') "
            "AND NVL(visibility,'content') = 'content'", d=days)
        for session_id, seq, role, content, when in cur.fetchall():
            rows.append({"table": "conversations", "when": when, "session_id": session_id,
                         "seq": seq, "role": role, "content": content})
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def expiry_counts(conn, days):
    """What the window currently catches, per raw table (content vs business split,
    so the report shows the privacy-floor effect too)."""
    counts = {}
    with conn.cursor() as cur:
        for table in RAW_TABLES:
            cur.execute(
                f"SELECT NVL(visibility,'content'), COUNT(*) FROM {table} "
                f"WHERE created_at < SYSTIMESTAMP - NUMTODSINTERVAL(:d, 'DAY') "
                f"GROUP BY NVL(visibility,'content')", d=days)
            counts[table] = {vis: n for vis, n in cur.fetchall()}
    return counts


def apply_expiry(conn, days):
    """Delete expired raw rows, both tables in ONE transaction (a half-done rotation
    never survives a crash). Returns rows deleted per table."""
    deleted = {}
    try:
        with conn.cursor() as cur:
            for table in RAW_TABLES:
                cur.execute(
                    f"DELETE FROM {table} "
                    f"WHERE created_at < SYSTIMESTAMP - NUMTODSINTERVAL(:d, 'DAY')", d=days)
                deleted[table] = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return deleted


def main():
    days = retention_days()
    if not days:
        print("memory expire: MEMORY_RETENTION_DAYS=0 — rotation disabled, nothing scanned")
        return
    apply = "--apply" in sys.argv
    conn = db.connect()
    try:
        counts = expiry_counts(conn, days)
        total = sum(n for t in counts.values() for n in t.values())
        for table, by_vis in counts.items():
            line = ", ".join(f"{v}={n}" for v, n in sorted(by_vis.items())) or "none"
            print(f"  {table} older than {days}d: {line}")
        if not apply:
            print(f"memory expire: {total} row(s) past the window (report only — rerun with --apply)")
            return
        if total == 0:
            print("memory expire: nothing past the window")
            return
        refusal = rotation_license(conn)
        if refusal:
            # fail LOUDLY so the sync marks the step failed — a missing or STALE distill
            # next to an old raw log means consolidation is broken, not that rotation is safe
            print(f"memory expire: REFUSING to rotate — {refusal}")
            sys.exit(1)
        archived = archive_expiring(conn, days)
        deleted = apply_expiry(conn, days)
        print("memory expire: rotated " +
              ", ".join(f"{t}={n}" for t, n in deleted.items()) +
              f" (retention {days}d; {archived} content row(s) tombstoned to "
              f"{TOMBSTONE.relative_to(ROOT)}; distilled layer retains the lessons)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
