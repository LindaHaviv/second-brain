# Data Export Guide

For **your own** content, official exports beat scraping: they're legal, complete (media +
metrics), and don't break when a platform changes its HTML. Trigger them in this order — the
slow ones first. Drop the downloaded bundles in an `exports/` folder (gitignored); the
normalizer scripts will read from there into `sources/`.

> **Start Instagram now.** Meta can take a few hours (sometimes longer). Everything else can be
> requested while it processes.

---

## 1. Instagram  *(primary — you crosspost from here)*

1. Open Instagram → **Profile → ☰ menu → Accounts Center**.
2. **Your information and permissions → Download your information → Download or transfer information**.
3. Select your **Instagram** account → **Some of your information** (pick *Posts, Reels, Stories,
   Comments, Profile*) or **All available information**.
4. Destination: **Download to device**.
5. **Date range: All time** · **Format: JSON** · **Media quality: High**.
6. Submit. You'll get an email with a download link when it's ready (often a few hours).

➡ Bundle is a `.zip` of JSON + media folders. Save to `exports/instagram/`.

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
