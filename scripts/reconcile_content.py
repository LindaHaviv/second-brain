"""Reconcile the content calendar with reality: Notion plans, the brain knows.

The Notion content tracker holds INTENT (what you planned, where, when). The posts
table holds REALITY (what the loaders saw actually go live, with real timestamps).
This step closes the loop, per platform on each tracker row:

  match found   -> stamp "Actual Post Date" (earliest match), append the live URL to
                   "Posted Links", and flip the phase to Complete once EVERY listed
                   platform has shipped. Facts only — planning fields are never touched.
  no match      -> the row lands in the report as planned-but-not-shipped (once its
                   expected date is comfortably past).
  extra posts   -> recent posts in the brain that match NO tracker row are reported as
                   shipped-but-never-tracked (the recurring cross-post gap report).

Deterministic: SQL + fuzzy title matching (difflib), no LLM. The report is saved into
the brain as a note ONLY when there is something to say — quiet days write nothing.

Config (oracle/.env):
  NOTION_TOKEN=...                       # same integration the Notion loader uses
  NOTION_CONTENT_TRACKER=<database id>   # the tracker DB; the sync step skips when unset
  Tracker properties expected: Title, Platform (multi-select), Expected Publish Date,
  Actual Post Date (date), Posted Links (rich_text), High Level Phase (status with a
  "Complete" option). Missing properties are tolerated (that part is just skipped).

Run:  ./.venv/bin/python scripts/reconcile_content.py [--dry-run]
"""
import datetime
import difflib
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
sys.path.insert(0, str(ROOT / "scripts"))
import db  # noqa: E402  (also resolves keychain:<item> env values)

PLATFORMS = {"Instagram": "instagram", "LinkedIn": "linkedin", "TikTok": "tiktok",
             "YouTube": "youtube", "X": "x", "Substack": "substack"}
MATCH_THRESHOLD = 0.55
BEFORE_DAYS, AFTER_DAYS = 21, 45   # candidate window around the expected date
NO_DATE_WINDOW = 90                # rows without an expected date: recent posts only
LATE_AFTER_DAYS = 3                # expected date this far past + no match = "late"
UNTRACKED_WINDOW = 30              # shipped-but-not-tracked lookback
COMPLETE = "Complete"


def normalize(s):
    """Lowercase, strip everything but word chars/spaces, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def score(a, b):
    """Similarity of two titles in [0,1]. Three signals, best wins: sequence ratio,
    containment (one title embedded in the other), and distinctive-word overlap —
    a planned title and a posted caption often share the *nouns* but not the
    phrasing ('The Friction That Built Datadog' vs 'Datadog was born out of the
    friction between Dev & Ops')."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    if len(na) >= 12 and len(nb) >= 12 and (na in nb or nb in na):
        ratio = max(ratio, 0.85)
    wa = {w for w in na.split() if len(w) >= 4}
    wb = {w for w in nb.split() if len(w) >= 4}
    if len(wa) >= 3 and len(wb) >= 3:
        overlap = len(wa & wb) / min(len(wa), len(wb))
        if overlap >= 0.5:
            ratio = max(ratio, 0.55 + 0.3 * overlap)
    return ratio


def best_match(row_title, candidates):
    """candidates: [(url, title, published_at)] -> (url, published_at) of the best
    title match above threshold, else None."""
    best, best_s = None, MATCH_THRESHOLD
    for url, title, published in candidates:
        s = score(row_title, title)
        if s > best_s:
            best, best_s = (url, published), s
    return best


def merge_links(existing, additions):
    """Append 'Platform: url' lines not already present; preserves existing text."""
    lines = [ln for ln in (existing or "").splitlines() if ln.strip()]
    have = {ln.split(": ", 1)[-1].strip() for ln in lines}
    for label, url in additions:
        if url not in have:
            lines.append(f"{label}: {url}")
            have.add(url)
    return "\n".join(lines)


def window_for(expected):
    if expected:
        return (expected - datetime.timedelta(days=BEFORE_DAYS),
                expected + datetime.timedelta(days=AFTER_DAYS))
    now = datetime.datetime.now()
    return (now - datetime.timedelta(days=NO_DATE_WINDOW), now)


# --- Notion I/O (thin; property shapes tolerated when absent) ---------------------

def _rich(arr):
    return "".join(s.get("plain_text", "") for s in (arr or []))


def source_id_of(notion, dbid):
    """Accept a database id OR a data-source id: databases carry their data_sources
    (Notion API 2025-09+); a database id resolves to its first source."""
    try:
        sources = notion.databases.retrieve(database_id=dbid).get("data_sources") or []
        return sources[0]["id"] if sources else dbid
    except Exception:
        return dbid   # already a data-source id (or retrieve not permitted) — try as-is


def tracker_rows(notion, dbid):
    """All tracker pages -> [{id, title, platforms, expected, actual, links, phase}]."""
    out, cursor = [], None
    sid = source_id_of(notion, dbid)
    while True:
        r = notion.data_sources.query(data_source_id=sid, start_cursor=cursor,
                                      page_size=100)
        for pg in r.get("results", []):
            props = pg.get("properties", {})
            title = ""
            for v in props.values():
                if v.get("type") == "title":
                    title = _rich(v.get("title"))
            plats = [o.get("name") for o in
                     (props.get("Platform", {}).get("multi_select") or [])]
            def _date(name):
                v = props.get(name, {})
                raw = (v.get("date") or {}).get("start") if v.get("type") == "date" else None
                try:
                    return datetime.datetime.fromisoformat(raw[:19]) if raw else None
                except ValueError:
                    return None
            phase = ((props.get("High Level Phase", {}).get("status") or {}).get("name"))
            out.append({"id": pg["id"], "title": title,
                        "platforms": [p for p in plats if p in PLATFORMS],
                        "expected": _date("Expected Publish Date"),
                        "actual": _date("Actual Post Date"),
                        "links": _rich(props.get("Posted Links", {}).get("rich_text")),
                        "phase": phase})
        if not r.get("has_more"):
            return out
        cursor = r.get("next_cursor")


def stamp(notion, row, actual, links, complete):
    props = {"Posted Links": {"rich_text": [{"text": {"content": links[:1900]}}]}}
    if actual and not row["actual"]:
        props["Actual Post Date"] = {"date": {"start": actual.date().isoformat()}}
    if complete and row["phase"] != COMPLETE:
        props["High Level Phase"] = {"status": {"name": COMPLETE}}
    notion.pages.update(page_id=row["id"], properties=props)


# --- main -------------------------------------------------------------------------

def candidates_for(cur, platform_id, lo, hi):
    cur.execute("""select url, title, published_at from posts
                   where platform_id = :p and url is not null
                     and published_at between :lo and :hi""",
                p=platform_id, lo=lo, hi=hi)
    return cur.fetchall()


def main():
    dry = "--dry-run" in sys.argv
    dbid = os.environ.get("NOTION_CONTENT_TRACKER")
    if not os.environ.get("NOTION_TOKEN") or not dbid:
        sys.exit("reconcile_content: set NOTION_TOKEN and NOTION_CONTENT_TRACKER "
                 "(the tracker database id) in oracle/.env")
    from notion_client import Client
    notion = Client(auth=os.environ["NOTION_TOKEN"])
    conn = db.connect()
    cur = conn.cursor()
    rows = tracker_rows(notion, dbid)
    now = datetime.datetime.now()
    stamped, late, matched_urls = [], [], set()
    for row in rows:
        for ln in row["links"].splitlines():          # already-recorded links count as
            matched_urls.add(ln.split(": ", 1)[-1].strip())   # matched for gap purposes
        if not row["platforms"] or (row["phase"] == COMPLETE and row["actual"]):
            continue
        lo, hi = window_for(row["expected"])
        found = []
        for plat in row["platforms"]:
            hit = best_match(row["title"], candidates_for(cur, PLATFORMS[plat], lo, hi))
            if hit:
                found.append((plat, hit[0], hit[1]))
                matched_urls.add(hit[0])
        if found:
            dates = [d for _, _, d in found if d]
            actual = min(dates) if dates else None
            links = merge_links(row["links"], [(p, u) for p, u, _ in found])
            complete = {p for p, _, _ in found} == set(row["platforms"])
            if not dry:
                stamp(notion, row, actual, links, complete)
            stamped.append(f"- {row['title']}: {', '.join(p for p, _, _ in found)}"
                           f"{' -> Complete' if complete else ''}")
        elif row["expected"] and (now - row["expected"]).days > LATE_AFTER_DAYS:
            late.append(f"- {row['title']} (expected {row['expected'].date()}, "
                        f"{', '.join(row['platforms'])})")
    # shipped-but-never-tracked: recent posts no tracker row accounts for
    row_titles = [r["title"] for r in rows if r["title"]]
    untracked, seen_titles = [], set()
    lo = now - datetime.timedelta(days=UNTRACKED_WINDOW)
    for plat, pid in PLATFORMS.items():
        for url, title, published in candidates_for(cur, pid, lo, now):
            if url in matched_urls:
                continue
            key = (plat, normalize(title)[:60])   # harvests can hold URL variants of
            if key in seen_titles:                # one post — report each post once
                continue
            if any(score(t, title) > MATCH_THRESHOLD for t in row_titles):
                continue
            seen_titles.add(key)
            when = published.date().isoformat() if published else "?"
            untracked.append(f"- [{plat}] {title} ({when}) {url}")
    parts = []
    if stamped:
        parts.append("STAMPED (calendar updated from reality):\n" + "\n".join(stamped))
    if late:
        parts.append("PLANNED BUT NOT SHIPPED (expected date passed, no post found):\n"
                     + "\n".join(late))
    if untracked:
        parts.append("SHIPPED BUT NEVER TRACKED (post exists, no calendar row):\n"
                     + "\n".join(untracked))
    if not parts:
        print("reconcile: calendar and reality agree — nothing to report")
        conn.close()
        return
    today = datetime.date.today().isoformat()
    body = (f"Content reconciliation for {today} - which planned posts actually "
            f"shipped, what is late, and what shipped untracked:\n\n" + "\n\n".join(parts))
    print(body + ("\n\n(dry run: nothing written)" if dry else ""))
    if not dry:
        import freshness
        freshness.save_note(conn, f"Content reconciliation {today}", body)
        print("\n(saved to the brain)")
    conn.close()


if __name__ == "__main__":
    main()
