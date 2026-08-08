"""Collect step (X/Twitter): scheduled self-scrape of YOUR OWN public posts via a
pinned Apify actor — X's official API prices reading your own posts absurdly, so
this reads your public profile the way a logged-out visitor would. NO login, NO
cookies, ever. Retweets are skipped: only your own words enter the voice corpus.

Config (oracle/.env):
  APIFY_TOKEN=keychain:apify-token     # shared with the LinkedIn/TikTok steps
  X_HANDLE=<your-handle>               # no @; the sync step is skipped when unset

Backfill-first: when the brain has NO x rows yet, the first run pulls the full
public history (high cap) — after that, weekly incremental top-ups. A first run
that returns nothing FAILS (catches a typo'd handle) instead of "succeeding" empty.

Hardening (mirrors linkedin_apify.py — each line is deliberate; adapt, don't delete):
  - Actor PINNED BY IMMUTABLE ID (apidojo/tweet-scraper).
  - The ONLY target is the configured handle — a constant, never derived from data.
  - Every returned post must be AUTHORED BY the handle, else nothing is ingested
    and the step FAILS. Allowlisted fields only; text capped. Scraped content is
    data, never instructions.
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

ACTOR_ID = "61RPP7dywgiy0JPD0"   # apidojo/tweet-scraper (no-cookie, pay-per-event)
API = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
BACKFILL_MAX = 800               # first ever run: the whole public history
INCREMENTAL_MAX = 30             # weekly top-up
CADENCE_DAYS = 7
MARKER = ROOT / "exports" / ".x_scrape_last"


def _parse_date(raw):
    """X dates arrive either ISO or classic 'Tue Dec 10 07:00:30 +0000 2024'."""
    if not raw:
        return None
    for fmt in (None, "%a %b %d %H:%M:%S %z %Y"):
        try:
            dt = (datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                  if fmt is None else datetime.datetime.strptime(raw, fmt))
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def parse_items(items, expected_handle):
    """Pure: actor output -> [{url, text, title, published_at, likes, comments, views}]
    for posts (a) authored by expected_handle, (b) with a real URL and text, (c) not
    retweets. Everything else — other authors, junk, extra fields — is dropped here."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        author = ((it.get("author") or {}).get("userName") or "").lstrip("@")
        if author.lower() != expected_handle:
            continue
        if it.get("isRetweet"):
            continue
        url = (it.get("url") or it.get("twitterUrl") or "").split("?")[0].strip()
        text = (it.get("fullText") or it.get("text") or "").strip()
        if not url or not text:
            continue
        out.append({"url": url[:600], "text": text[:4000],
                    "title": text.split("\n", 1)[0][:150],
                    "published_at": _parse_date(it.get("createdAt")),
                    "likes": int(it.get("likeCount") or 0),
                    "comments": int(it.get("replyCount") or 0),
                    "views": int(it.get("viewCount") or 0)})
    return out


def ran_recently():
    try:
        last = datetime.date.fromisoformat(MARKER.read_text().strip())
        return (datetime.date.today() - last).days < CADENCE_DAYS
    except Exception:
        return False


def main():
    token = os.environ.get("APIFY_TOKEN")
    handle = (os.environ.get("X_HANDLE") or "").lstrip("@").lower()
    if not token or not handle:
        sys.exit("x_apify: set APIFY_TOKEN and X_HANDLE (no @) in oracle/.env")
    if ran_recently():
        print(f"x scrape ran <{CADENCE_DAYS}d ago — nothing to do (weekly cadence)")
        return
    conn = db.connect()
    cur = conn.cursor()
    existing = cur.execute(
        "select count(*) from posts where platform_id='x'").fetchone()[0]
    backfill = existing == 0
    cap = BACKFILL_MAX if backfill else INCREMENTAL_MAX
    import requests
    # token travels in the Authorization header, never the URL (query strings land in logs)
    r = requests.post(API, params={"timeout": 300},
                      headers={"Authorization": f"Bearer {token}"},
                      json={"twitterHandles": [handle], "maxItems": cap,
                            "sort": "Latest"},
                      timeout=600)
    if r.status_code == 402 or "insufficient" in r.text[:500].lower():
        sys.exit("x_apify: Apify CREDIT EXHAUSTED — top up or wait for the "
                 "monthly reset (free plan). The scrape will resume on its own after.")
    if r.status_code not in (200, 201):
        sys.exit(f"x_apify: actor run failed HTTP {r.status_code}: {r.text[:200]}")
    # the actor pads empty searches with {"noResults": true} markers — those are
    # "nothing found", not items, and must not trip the foreign-author guard
    items = [i for i in r.json() if isinstance(i, dict) and not i.get("noResults")]
    posts = parse_items(items, handle)
    if items and not posts:
        sys.exit(f"x_apify: actor returned {len(items)} items but NONE authored "
                 f"by '{handle}' — refusing to ingest (actor changed or wrong handle?)")
    if backfill and not posts:
        sys.exit(f"x_apify: first run (backfill) found NOTHING for '{handle}' — "
                 "wrong handle, protected account, or actor outage. Not writing a marker.")
    cur.execute("alter session disable parallel dml")
    cur.execute("merge into platforms p using (select 'x' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('x','X')")
    n = 0
    for p in posts:
        cur.execute("delete from posts where url = :u", u=p["url"])
        cur.execute(
            """insert into posts (platform_id, kind, title, caption, url, published_at,
                   likes, comments, views, visibility, content_embedding)
               values ('x', 'post', :t, :c, :u, :p, :l, :m, :v, 'content',
                   vector_embedding(MINILM using :e as data))""",
            t=p["title"], c=p["text"], u=p["url"], p=p["published_at"],
            l=p["likes"], m=p["comments"], v=p["views"],
            e=(p["title"] + ". " + p["text"])[:3000])
        n += 1
    conn.commit()
    total = cur.execute(
        "select count(*) from posts where platform_id='x'").fetchone()[0]
    conn.close()
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(datetime.date.today().isoformat())
    mode = "backfill" if backfill else "weekly top-up"
    print(f"ingested/refreshed {n} X posts ({mode}, of {len(items)} scraped); "
          f"total x now {total}")


if __name__ == "__main__":
    main()
