"""One-shot: re-derive published_at for LinkedIn posts from the id in their URL.

Why: rows imported from relative labels ("3yr") got approximate dates — "N years ago"
collapsed onto the import day (a July 4/5 cluster), off by up to a year. The exact
publish time sits in the URL itself: every activity/ugcPost/share id carries its
creation time in its top 41 bits (see linkedin_ids.py). Rows the Apify actor dated
already agree with the id to the second, so this pass leaves them alone.

Dry run by default — prints what would change (count + examples), writes nothing:
  ./.venv/bin/python scripts/linkedin_backfill_dates.py
  ./.venv/bin/python scripts/linkedin_backfill_dates.py --apply   # write, one transaction

Rows without an id in the URL (the lh-<hash> rows the JSON importer minted) cannot be
fixed from here and are reported as 'no id in url'. Only published_at changes; text,
embeddings, engagement and visibility are untouched.
"""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
sys.path.insert(0, str(ROOT / "scripts"))
import db  # noqa: E402
from linkedin_ids import published_at_from_url  # noqa: E402

# The actor truncates milliseconds; anything this close is already the exact time.
TOLERANCE = datetime.timedelta(seconds=2)
EXAMPLES = 6


def plan(rows, tolerance=TOLERANCE):
    """Pure: [(post_id, url, published_at, visibility)] ->
    (changes=[(post_id, url, old, new, visibility)], already_exact, no_id).
    `new` is naive UTC, matching how every loader stores published_at."""
    changes, exact, no_id = [], 0, 0
    for pid, url, old, vis in rows:
        when = published_at_from_url(url)
        if when is None:
            no_id += 1
            continue
        new = when.replace(tzinfo=None)
        if old is not None and abs(new - old) <= tolerance:
            exact += 1
            continue
        changes.append((pid, url, old, new, vis))
    return changes, exact, no_id


def main():
    apply = "--apply" in sys.argv
    conn = db.open_connection()
    cur = conn.cursor()
    cur.execute("select post_id, url, published_at, visibility from posts "
                "where platform_id = 'linkedin' order by post_id")
    rows = cur.fetchall()
    changes, exact, no_id = plan(rows)

    print(f"linkedin rows: {len(rows)}  |  would fix: {len(changes)}  |  "
          f"already exact: {exact}  |  no id in url: {no_id}")
    if changes:
        years_off = sum(1 for _, _, old, new, _ in changes if old is None or old.year != new.year)
        print(f"  of which wrong YEAR (not just day): {years_off}")
        # examples come from content-scope rows only — this report may be pasted around
        shown = [c for c in changes if (c[4] or "content") == "content"]
        shown.sort(key=lambda c: abs(c[3] - c[2]) if c[2] else datetime.timedelta.max,
                   reverse=True)
        print("  largest corrections (stored -> exact, UTC):")
        for pid, url, old, new, _ in shown[:EXAMPLES]:
            print(f"    #{pid}  {old}  ->  {new.replace(microsecond=0)}   {url}")

    if apply and changes:
        cur.execute("alter session disable parallel dml")
        cur.executemany("update posts set published_at = :n where post_id = :p",
                        [{"n": new, "p": pid} for pid, _, _, new, _ in changes])
        conn.commit()
        print(f"applied: {len(changes)} rows updated (one transaction)")
    elif not apply:
        print("(dry run: nothing written — rerun with --apply to write)")
    conn.close()


if __name__ == "__main__":
    main()
