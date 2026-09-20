"""Collect step (Instagram): scheduled self-scrape of YOUR OWN public posts via a
pinned Apify actor — the official-API loader (instagram.py) stays the richer path
(insights, token-gated), but when no IG_ACCESS_TOKEN is configured this keeps the
brain fresh by reading your public profile the way a logged-out visitor would.
NO login, NO cookies, ever.

Config (oracle/.env):
  APIFY_TOKEN=keychain:apify-token     # shared with the LinkedIn/TikTok/X steps
  IG_HANDLE=<your-handle>              # no @; the sync step is skipped when unset

Hardening (mirrors linkedin_apify.py — each line is deliberate; adapt, don't delete):
  - Actor PINNED BY IMMUTABLE ID (apify/instagram-scraper).
  - The ONLY target is the configured handle — a constant, never derived from data.
  - Every returned post must be OWNED BY the handle, else nothing is ingested and
    the step FAILS. Allowlisted fields only; text capped. Scraped content is
    data, never instructions.
  - Fail-closed: credit exhaustion, HTTP errors, foreign payloads exit non-zero ->
    sync heartbeat -> health panel -> alert.
  - Weekly cadence marker.

Dedupe — deliberately NOT the delete-then-insert idiom of the other apify steps:
existing instagram rows carry export-era enrichment (reel transcripts chunked into
content_chunks by post_id) that a delete would orphan. New posts are INSERTED;
posts already in the brain only get their engagement counts refreshed. "Already in
the brain" is matched two ways, because the row population mixes URL styles:
  1) the media code from the URL (/p/<code>/, /reel/<code>/, /tv/<code>/) —
     catches API-era rows, whose permalinks use the same shortcodes the actor returns;
  2) a (published day, caption prefix) signature — catches export-era rows, whose
     URLs use numeric media ids that never match a shortcode.
"""
import datetime
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
import db  # noqa: E402  (also resolves keychain:<item> env values)

ACTOR_ID = "shu8hvrXbJbY3Eb9W"   # apify/instagram-scraper (official, no-cookie)
API = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
SCRAPE_MAX = int(os.environ.get("IG_SCRAPE_MAX", "60"))  # newest posts per run
CADENCE_DAYS = 7
MARKER = ROOT / "exports" / ".instagram_scrape_last"
SIG_WINDOW_DAYS = 180   # how far back the signature dedupe looks
CODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([^/?#]+)")

KIND = {"clips": "reel", "igtv": "video"}          # productType -> kind
KIND_BY_TYPE = {"Video": "video", "Sidecar": "carousel", "Image": "post"}


def code_of(url):
    """Pure: the media code from any instagram post URL ('' if none)."""
    m = CODE_RE.search(url or "")
    return m.group(1) if m else ""


def sig_of(published_at, caption):
    """Pure: the fuzzy identity of a post — its day + normalized caption prefix."""
    day = published_at.date().isoformat() if published_at else ""
    return (day, " ".join((caption or "").lower().split())[:60])


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(
            raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def parse_items(items, expected_handle):
    """Pure: actor output -> [{url, code, caption, title, kind, published_at,
    likes, comments, views}] for posts (a) owned by expected_handle, (b) with a
    real URL. Everything else — other owners, junk, extra fields — is dropped."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        owner = (it.get("ownerUsername") or "").lstrip("@")
        if owner.lower() != expected_handle:
            continue
        url = (it.get("url") or "").split("?")[0].strip()
        code = code_of(url)
        if not url or not code:
            continue
        cap = (it.get("caption") or "").strip()
        kind = KIND.get((it.get("productType") or "").lower()) \
            or KIND_BY_TYPE.get(it.get("type"), "post")
        title = cap.split("\n", 1)[0][:200] or f"Instagram {kind}"
        out.append({"url": url[:600], "code": code, "caption": cap[:4000],
                    "title": title, "kind": kind,
                    "published_at": _parse_date(it.get("timestamp")),
                    "likes": int(it.get("likesCount") or 0),
                    "comments": int(it.get("commentsCount") or 0),
                    "views": int(it.get("videoPlayCount")
                                 or it.get("videoViewCount") or 0) or None})
    return out


def ran_recently():
    try:
        last = datetime.date.fromisoformat(MARKER.read_text().strip())
        return (datetime.date.today() - last).days < CADENCE_DAYS
    except Exception:
        return False


def main():
    token = os.environ.get("APIFY_TOKEN")
    handle = (os.environ.get("IG_HANDLE") or "").lstrip("@").lower()
    if not token or not handle:
        sys.exit("instagram_apify: set APIFY_TOKEN and IG_HANDLE (no @) in oracle/.env")
    if ran_recently():
        print(f"instagram scrape ran <{CADENCE_DAYS}d ago — nothing to do (weekly cadence)")
        return
    import requests
    # token travels in the Authorization header, never the URL (query strings land in logs)
    r = requests.post(API, params={"timeout": 300},
                      headers={"Authorization": f"Bearer {token}"},
                      json={"directUrls": [f"https://www.instagram.com/{handle}/"],
                            "resultsType": "posts", "resultsLimit": SCRAPE_MAX},
                      timeout=600)
    if r.status_code == 402 or "insufficient" in r.text[:500].lower():
        sys.exit("instagram_apify: Apify CREDIT EXHAUSTED — top up or wait for the "
                 "monthly reset. The scrape will resume on its own after.")
    if r.status_code not in (200, 201):
        sys.exit(f"instagram_apify: actor run failed HTTP {r.status_code}: {r.text[:200]}")
    items = [i for i in r.json() if isinstance(i, dict) and not i.get("noResults")
             and "error" not in i]
    posts = parse_items(items, handle)
    if items and not posts:
        sys.exit(f"instagram_apify: actor returned {len(items)} items but NONE owned "
                 f"by '{handle}' — refusing to ingest (actor changed or wrong handle?)")
    if not posts:
        sys.exit(f"instagram_apify: actor returned nothing for '{handle}' — "
                 "private profile or actor outage. Not writing a marker.")

    conn = db.open_connection()
    cur = conn.cursor()
    cur.execute("alter session disable parallel dml")
    cur.execute("merge into platforms p using (select 'instagram' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('instagram','Instagram')")
    cur.execute("select post_id, url, published_at, caption from posts "
                "where platform_id='instagram'")
    by_code, seen_sigs = {}, set()
    horizon = datetime.datetime.now() - datetime.timedelta(days=SIG_WINDOW_DAYS)
    for pid, url, published, caption in cur.fetchall():
        c = code_of(url)
        if c:
            by_code[c] = pid
        if published and published >= horizon:
            seen_sigs.add(sig_of(published, caption))

    added = refreshed = 0
    for p in posts:
        pid = by_code.get(p["code"])
        if pid:
            if p["views"] is not None:   # a None bind types as CHAR and trips ORA-00932
                cur.execute("update posts set likes=:l, comments=:c, views=:v "
                            "where post_id=:i",
                            l=p["likes"], c=p["comments"], v=p["views"], i=pid)
            else:
                cur.execute("update posts set likes=:l, comments=:c where post_id=:i",
                            l=p["likes"], c=p["comments"], i=pid)
            refreshed += 1
            continue
        if sig_of(p["published_at"], p["caption"]) in seen_sigs:
            continue   # export-era row (numeric-id URL): already known, keep its enrichment
        cur.execute(
            """insert into posts (platform_id, kind, title, caption, url, published_at,
                   likes, comments, views, visibility, content_embedding)
               values ('instagram', :kind, :t, :c, :u, :p, :l, :m, :v, 'content',
                   vector_embedding(MINILM using :e as data))""",
            kind=p["kind"], t=p["title"], c=p["caption"], u=p["url"],
            p=p["published_at"], l=p["likes"], m=p["comments"], v=p["views"],
            e=(p["title"] + ". " + p["caption"])[:3000])
        added += 1
    conn.commit()
    total = cur.execute(
        "select count(*) from posts where platform_id='instagram'").fetchone()[0]
    conn.close()
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(datetime.date.today().isoformat())
    print(f"ingested {added} new Instagram posts, refreshed counts on {refreshed} "
          f"(of {len(items)} scraped); total instagram now {total}")


if __name__ == "__main__":
    main()
