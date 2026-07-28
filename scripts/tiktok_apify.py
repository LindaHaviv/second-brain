"""Collect step (TikTok): scheduled self-scrape of YOUR OWN public videos via a
pinned Apify actor — TikTok's official API needs per-app approval, so this reads
your public profile the way a logged-out visitor would. NO login, NO cookies, ever.

Config (oracle/.env):
  APIFY_TOKEN=keychain:apify-token     # shared with the LinkedIn step
  TIKTOK_HANDLE=<your-handle>          # no @; the sync step is skipped when unset

Backfill-first: when the brain has NO tiktok rows yet, the first run pulls the full
public history (high cap) — after that, weekly incremental top-ups. A first run that
returns nothing FAILS (catches a typo'd handle) instead of "succeeding" empty.

Hardening (mirrors linkedin_apify.py — each line is deliberate; adapt, don't delete):
  - Actor PINNED BY IMMUTABLE ID (clockworks/tiktok-profile-scraper).
  - The ONLY target is the configured handle — a constant, never derived from data.
  - Every returned video must be AUTHORED BY the handle, else nothing is ingested
    and the step FAILS (an actor bug/swap must not poison the voice corpus).
  - Allowlisted fields only (url, caption, date, engagement counts); text capped.
    Scraped content is data, never instructions.
  - Fail-closed: credit exhaustion, HTTP errors, foreign payloads exit non-zero ->
    sync heartbeat -> health panel -> alert.
  - Weekly cadence marker; dedupe by URL, delete-then-insert (refreshes counts).
"""
import datetime
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
import db  # noqa: E402  (also resolves keychain:<item> env values)

ACTOR_ID = "0FXVyOXXEmdGcV88a"   # clockworks/tiktok-profile-scraper (no-cookie, pay-per-event)
API = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
BACKFILL_MAX = 500               # first ever run: the whole public history
INCREMENTAL_MAX = 25             # weekly top-up
CADENCE_DAYS = 7
MARKER = ROOT / "exports" / ".tiktok_scrape_last"


def parse_items(items, expected_handle):
    """Pure: actor output -> [{url, text, title, published_at, likes, comments, views}]
    for videos (a) authored by expected_handle, (b) with a real URL. Everything else —
    other authors, junk, extra fields — is dropped here."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        author = ((it.get("authorMeta") or {}).get("name") or "").lstrip("@")
        if author.lower() != expected_handle:
            continue
        url = (it.get("webVideoUrl") or "").split("?")[0].strip()
        if not url:
            continue
        text = (it.get("text") or "").strip()
        published = None
        raw = it.get("createTimeISO")
        if raw:
            try:
                published = datetime.datetime.fromisoformat(
                    raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                published = None
        title = text.split("\n", 1)[0][:150] or "TikTok video"
        out.append({"url": url[:600], "text": text[:4000], "title": title,
                    "published_at": published,
                    "likes": int(it.get("diggCount") or 0),
                    "comments": int(it.get("commentCount") or 0),
                    "views": int(it.get("playCount") or 0)})
    return out


def ran_recently():
    try:
        last = datetime.date.fromisoformat(MARKER.read_text().strip())
        return (datetime.date.today() - last).days < CADENCE_DAYS
    except Exception:
        return False


def main():
    token = os.environ.get("APIFY_TOKEN")
    handle = (os.environ.get("TIKTOK_HANDLE") or "").lstrip("@").lower()
    if not token or not handle:
        sys.exit("tiktok_apify: set APIFY_TOKEN and TIKTOK_HANDLE (no @) in oracle/.env")
    if ran_recently():
        print(f"tiktok scrape ran <{CADENCE_DAYS}d ago — nothing to do (weekly cadence)")
        return
    conn = db.connect()
    cur = conn.cursor()
    existing = cur.execute(
        "select count(*) from posts where platform_id='tiktok'").fetchone()[0]
    backfill = existing == 0
    cap = BACKFILL_MAX if backfill else INCREMENTAL_MAX
    import requests
    r = requests.post(API, params={"timeout": 300, "token": token},
                      json={"profiles": [handle], "resultsPerPage": cap,
                            "profileScrapeSections": ["videos"],
                            "excludePinnedPosts": False},
                      timeout=600)
    if r.status_code == 402 or "insufficient" in r.text[:500].lower():
        sys.exit("tiktok_apify: Apify CREDIT EXHAUSTED — top up or wait for the "
                 "monthly reset (free plan). The scrape will resume on its own after.")
    if r.status_code not in (200, 201):
        sys.exit(f"tiktok_apify: actor run failed HTTP {r.status_code}: {r.text[:200]}")
    items = r.json()
    posts = parse_items(items, handle)
    if items and not posts:
        sys.exit(f"tiktok_apify: actor returned {len(items)} items but NONE authored "
                 f"by '{handle}' — refusing to ingest (actor changed or wrong handle?)")
    if backfill and not posts:
        sys.exit(f"tiktok_apify: first run (backfill) found NOTHING for '{handle}' — "
                 "wrong handle, private account, or actor outage. Not writing a marker.")
    cur.execute("alter session disable parallel dml")
    cur.execute("merge into platforms p using (select 'tiktok' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('tiktok','TikTok')")
    n = 0
    for p in posts:
        cur.execute("delete from posts where url = :u", u=p["url"])
        cur.execute(
            """insert into posts (platform_id, kind, title, caption, url, published_at,
                   likes, comments, views, visibility, content_embedding)
               values ('tiktok', 'video', :t, :c, :u, :p, :l, :m, :v, 'content',
                   vector_embedding(MINILM using :e as data))""",
            t=p["title"], c=p["text"], u=p["url"], p=p["published_at"],
            l=p["likes"], m=p["comments"], v=p["views"],
            e=(p["title"] + ". " + p["text"])[:3000])
        n += 1
    conn.commit()
    total = cur.execute(
        "select count(*) from posts where platform_id='tiktok'").fetchone()[0]
    conn.close()
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(datetime.date.today().isoformat())
    mode = "backfill" if backfill else "weekly top-up"
    print(f"ingested/refreshed {n} TikTok videos ({mode}, of {len(items)} scraped); "
          f"total tiktok now {total}")


if __name__ == "__main__":
    main()
