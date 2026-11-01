"""Collect step (Threads): scheduled self-scrape of YOUR OWN public Threads posts via a
pinned Apify actor. Threads has no self-serve read API for a creator's own history;
this reads your public profile the way a logged-out visitor would. NO login, NO
cookies, ever. Reposts and replies are skipped: only your own top-level words enter
the voice corpus.

Config (oracle/.env):
  APIFY_TOKEN=keychain:apify-token     # shared with the LinkedIn/TikTok/X steps
  THREADS_HANDLE=<your-handle>         # no @; the sync step is skipped when unset

Backfill-first, weekly cadence, fail-closed, authorship-asserted, allowlisted fields,
delete-then-insert by URL (refreshes counts) — the x_apify.py contract, unchanged.
"""
import datetime
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
import db  # noqa: E402  (also resolves keychain:<item> env values)

ACTOR_ID = "lct1dlYksEDIG9If9"   # futurizerush/meta-threads-scraper (no-login, pay-per-event)
API = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
BACKFILL_MAX = 500
INCREMENTAL_MAX = 30
CADENCE_DAYS = 7
MARKER = ROOT / "exports" / ".threads_scrape_last"


def _parse_date(it):
    ts = it.get("created_at_timestamp")
    if ts:
        try:
            return datetime.datetime.utcfromtimestamp(int(ts))
        except (TypeError, ValueError, OSError):
            pass
    raw = it.get("created_at") or ""
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def parse_items(items, expected_handle):
    """Pure: actor rows -> [{url, text, title, kind, published_at, likes, comments, views,
    media_url}] for posts (a) authored by expected_handle, (b) top-level (not reply, not
    repost), (c) with a URL and text. Everything else is dropped here."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if (it.get("username") or "").lstrip("@").lower() != expected_handle:
            continue
        if it.get("is_reply") or it.get("is_repost") or it.get("record_type") not in (None, "", "post"):
            continue
        url = (it.get("post_url") or "").split("?")[0].strip()
        text = (it.get("text_content") or "").strip()
        if not url or not text:
            continue
        kind = "video" if (it.get("media_type") or "").lower() == "video" else "post"
        out.append({"url": url[:600], "text": text[:4000],
                    "title": text.split("\n", 1)[0][:150], "kind": kind,
                    "published_at": _parse_date(it),
                    "likes": int(it.get("like_count") or 0),
                    "comments": int(it.get("reply_count") or 0),
                    "views": int(it.get("view_count") or 0) or None,
                    "media_url": (it.get("media_url") or "")[:1000] if kind == "video" else ""})
    return out


def ran_recently():
    try:
        last = datetime.date.fromisoformat(MARKER.read_text().strip())
        return (datetime.date.today() - last).days < CADENCE_DAYS
    except Exception:
        return False


def main():
    token = os.environ.get("APIFY_TOKEN")
    handle = (os.environ.get("THREADS_HANDLE") or "").lstrip("@").lower()
    if not token or not handle:
        sys.exit("threads_apify: set APIFY_TOKEN and THREADS_HANDLE (no @) in oracle/.env")
    if ran_recently():
        print(f"threads scrape ran <{CADENCE_DAYS}d ago — nothing to do (weekly cadence)")
        return
    conn = db.open_connection()
    cur = conn.cursor()
    existing = cur.execute("select count(*) from posts where platform_id='threads'").fetchone()[0]
    backfill = existing == 0
    cap = BACKFILL_MAX if backfill else INCREMENTAL_MAX
    import requests
    r = requests.post(API, params={"timeout": 300},
                      headers={"Authorization": f"Bearer {token}"},
                      json={"mode": "user", "usernames": [handle], "max_posts": cap},
                      timeout=600)
    if r.status_code == 402 or "insufficient" in r.text[:500].lower():
        sys.exit("threads_apify: Apify CREDIT EXHAUSTED — top up; the scrape resumes on its own.")
    if r.status_code not in (200, 201):
        sys.exit(f"threads_apify: actor run failed HTTP {r.status_code}: {r.text[:200]}")
    items = [i for i in r.json() if isinstance(i, dict) and not i.get("noResults")]
    posts = parse_items(items, handle)
    if items and not posts:
        sys.exit(f"threads_apify: actor returned {len(items)} rows but NONE are top-level posts "
                 f"by '{handle}' — refusing to ingest (actor changed or wrong handle?)")
    if backfill and not posts:
        sys.exit(f"threads_apify: first run found NOTHING for '{handle}' — wrong handle, "
                 "private account, or actor outage. Not writing a marker.")
    cur.execute("alter session disable parallel dml")
    cur.execute("merge into platforms p using (select 'threads' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('threads','Threads')")
    n = 0
    for p in posts:
        cur.execute("delete from posts where url = :u", u=p["url"])
        cur.execute(
            """insert into posts (platform_id, kind, title, caption, url, published_at,
                   likes, comments, views, visibility, content_embedding)
               values ('threads', :k, :t, :c, :u, :p, :l, :m, :v, 'content',
                   vector_embedding(MINILM using :e as data))""",
            k=p["kind"], t=p["title"], c=p["text"], u=p["url"], p=p["published_at"],
            l=p["likes"], m=p["comments"], v=p["views"],
            e=(p["title"] + ". " + p["text"])[:3000])
        if p["media_url"]:
            cur.execute("""insert into media (post_id, kind, url)
                           select post_id, 'video', :mu from posts where url = :u""",
                        mu=p["media_url"], u=p["url"])
        n += 1
    conn.commit()
    total = cur.execute("select count(*) from posts where platform_id='threads'").fetchone()[0]
    conn.close()
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(datetime.date.today().isoformat())
    print(f"ingested/refreshed {n} Threads posts ({'backfill' if backfill else 'weekly top-up'}, "
          f"of {len(items)} scraped); total threads now {total}")


if __name__ == "__main__":
    main()
