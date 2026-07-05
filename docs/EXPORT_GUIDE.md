# Data Export Guide

For **your own** content, official exports beat scraping: they're legal, complete (media +
metrics), and don't break when a platform changes its HTML. Trigger them in this order — the
slow ones first. Drop the downloaded bundles in an `exports/` folder (gitignored); the
normalizer scripts will read from there into `sources/`.

> **Start Instagram now.** Meta can take a few hours (sometimes longer). Everything else can be
> requested while it processes.

---

## 1. Instagram  *(primary — you crosspost from here)*

**Recommended: the API (auto-syncs new posts + performance).** For a **Creator/Business** account,
the *Instagram API with Instagram Login* pulls your media and engagement/reach on a schedule — **no
Facebook Page required, no scraping**. One-time setup:

1. **developers.facebook.com → Create app → type "Business".**
2. In the app: **Add product → Instagram → "API setup with Instagram login".**
3. **Generate access token →** connect your Creator account, grant **`instagram_business_basic`** +
   **`instagram_business_manage_insights`**. Copy the (short-lived) token shown.
4. In **App settings → Basic**, copy the **App secret**.
5. Mint a long-lived (~60-day) token:
   ```bash
   IG_APP_SECRET=<app secret> ./.venv/bin/python scripts/instagram_token.py <short-lived token>
   ```
   Paste the printed `IG_ACCESS_TOKEN=...` into `oracle/.env` (it's a **secret** — keep it out of git).
6. Load it (incremental — only adds new media each run):
   ```bash
   ./.venv/bin/python scripts/instagram.py
   ```
   Refresh the token every ~60 days with `scripts/instagram_token.py --refresh` (set a reminder —
   the scheduled sync pulls new posts but does **not** refresh the token).

**Alternative: one-time export** (good for a full historical backfill):
Instagram → **Profile → ☰ → Accounts Center → Your information and permissions → Download your
information**. Pick *Posts, Reels, Stories, Comments, Profile*, **Format: JSON**, **All time**,
download to device. ➡ Save the `.zip` to `exports/instagram/`.

## 2. LinkedIn

LinkedIn's official data archive is unreliable for post content: rich-media posts (video
especially) often arrive without their caption text. The dependable path is harvesting your own
activity feed from a logged-in browser session — your posts are all there, with full text.

1. Log in and open `linkedin.com/in/<you>/recent-activity/all/`.
2. Scroll to the bottom of your history, collecting each post's URN, author, text, relative age,
   and media type. An AI browser assistant can do the scrolling and collecting for you (keep it
   supervised — it's your logged-in session); the target JSON shape is documented in
   `scripts/linkedin_harvest.py`:

   ```json
   {"harvested_at": "...", "items": [
     {"urn": "urn:li:activity:...", "actor": "Your Name", "header": "",
      "text": "full post text", "rel": "3yr", "media": "video"}]}
   ```

3. Save as `linkedin_harvest.json` and load it — only your original posts are kept (set `LINKEDIN_ACTOR` to your display name; reposts of
   other people's content are filtered by the `actor` field), and reruns are dedupe-safe:

   ```bash
   ./.venv/bin/python scripts/linkedin_harvest.py ~/Downloads/linkedin_harvest.json
   ```

Relative ages ("3yr") become approximate dates — good enough for search and voice mining.
*(Optional: request the official archive too — **Settings & Privacy → Data Privacy → Get a copy
of your data → larger archive** — and if its `Shares.csv` has usable text for your posts, its
exact dates can complement the harvest.)*

## 3. TikTok

1. **Profile → ☰ → Settings and privacy → Account → Download your data**.
2. **Request data** · **File format: JSON** (not TXT).
3. When ready (under *Download data* tab), download the `.zip`.

➡ Save to `exports/tiktok/`. *(No loader ships for TikTok yet — copy any `scripts/` loader as a
template; the target is always the same `posts` contract.)*

## 4. YouTube

1. Go to **takeout.google.com**.
2. Deselect all → select **YouTube and YouTube Music**.
3. **All YouTube data included** → keep *videos* + *metadata* (history optional).
4. Export → choose `.zip`, one-time. Email when ready.

➡ Save to `exports/youtube/`. *(Note: `scripts/youtube.py` reads **yt-dlp** `.jsonl` output — see the
tutorial — not the Takeout format. Takeout is still worth keeping as your own full backup.)*

## 5. X / Twitter

1. **Settings → Your account → Download an archive of your data**.
2. Verify identity → **Request archive**. Can take up to **24 hours**.
3. Download when the email arrives.

➡ Save to `exports/twitter/`. (Archive is HTML/JS + a `data/` folder of JSON.) *(No loader ships for
X yet — copy any `scripts/` loader as a template.)*

---

## After exports land

Run the matching loader (`scripts/<platform>.py`) for each bundle — and for platforms without one,
**copy an existing loader as a template**: they all end at the same contract (map the export's
fields to `title`, `caption`, `url`, `published_at`, insert into `posts`; the embedding is
generated in-DB). Then run `classify_private.py` and `sync.py` to fold the new content in.

## What about content you can't export?

If something only exists live (e.g. a repost you don't own), prefer each platform's **official API**
over scraping — logins + anti-bot + terms of service make scraping an account risk.
