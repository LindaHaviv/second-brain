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
   IG_APP_SECRET=<app secret> ../.venv/bin/python scripts/instagram_token.py <short-lived token>
   ```
   Paste the printed `IG_ACCESS_TOKEN=...` into `oracle/.env` (it's a **secret** — keep it out of git).
6. Load it (incremental — only adds new media each run):
   ```bash
   ../.venv/bin/python scripts/instagram.py
   ```
   Refresh the token every ~60 days: `scripts/instagram_token.py --refresh`. The scheduled sync
   (below) does both for you.

**Alternative: one-time export** (good for a full historical backfill):
Instagram → **Profile → ☰ → Accounts Center → Your information and permissions → Download your
information**. Pick *Posts, Reels, Stories, Comments, Profile*, **Format: JSON**, **All time**,
download to device. ➡ Save the `.zip` to `exports/instagram/`.

## 2. LinkedIn

1. **Settings & Privacy → Data Privacy → Get a copy of your data**.
2. Choose **"Download larger data archive…"** (includes posts/articles/media) — *not* just the fast one.
3. Request. Larger archive can take up to **24 hours**; you'll get an email.

➡ Save to `exports/linkedin/`. (CSV + media.)

## 3. TikTok

1. **Profile → ☰ → Settings and privacy → Account → Download your data**.
2. **Request data** · **File format: JSON** (not TXT).
3. When ready (under *Download data* tab), download the `.zip`.

➡ Save to `exports/tiktok/`.

## 4. YouTube

1. Go to **takeout.google.com**.
2. Deselect all → select **YouTube and YouTube Music**.
3. **All YouTube data included** → keep *videos* + *metadata* (history optional).
4. Export → choose `.zip`, one-time. Email when ready.

➡ Save to `exports/youtube/`. (Also have a YouTube Data API key ready if you want live metrics.)

## 5. X / Twitter

1. **Settings → Your account → Download an archive of your data**.
2. Verify identity → **Request archive**. Can take up to **24 hours**.
3. Download when the email arrives.

➡ Save to `exports/twitter/`. (Archive is HTML/JS + a `data/` folder of JSON — we parse the JSON.)

---

## After exports land

Tell me which bundles are in `exports/` and I'll write the per-platform normalizer
(`scripts/<platform>.{py,js}`) that converts each into `sources/<platform>/*.md` using the
frontmatter schema in the README. Instagram first.

## What about content you can't export?

If something only exists live (e.g. a repost you don't own, or older deleted-export content),
that's the fallback case for scraping with the connected Puppeteer browser — but we only reach
for it when an export genuinely can't cover it.
