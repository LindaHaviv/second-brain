# Tutorial — from zero to a research agent over your own content

A guided, first-steps walkthrough. By the end you'll have, running **locally on your machine**:
a content store, semantic search over it, and a Claude research agent that answers grounded
in *your* content and remembers what it found.

Three parts: **set it up → see it work → make it yours** — plus an optional **Part 4** to
level up with a self-improving knowledge wiki and using your brain from Claude Desktop.

---

## Part 1 — Set it up locally (~15 min)

Everything runs locally and headless — no Docker Desktop, no cloud account.

**Prerequisites:** macOS + [Homebrew](https://brew.sh). (Apple Silicon assumed; on Intel,
remove the `platform:` line in `oracle/docker-compose.yml`.)

```bash
# container engine (headless)
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60

# python env (3.12)
brew install python@3.12
python3.12 -m venv .venv
./.venv/bin/pip install -r oracle/agent/requirements.txt yt-dlp

# config (local demo passwords are fine)
cp oracle/.env.example oracle/.env

# start Oracle AI Database 26ai (Free) in a container
docker-compose -f oracle/docker-compose.yml up -d

# download the embedding model + set up the database (schema, model, grants)
./oracle/download-model.sh
./oracle/bootstrap.sh
```

✅ **Checkpoint** — confirm the database is the real thing and ready:

```bash
./.venv/bin/python -c "import sys; sys.path.insert(0,'oracle/agent'); import db; \
  print(db.connect().cursor().execute( \
  \"select product from product_component_version where product like 'Oracle%'\").fetchone()[0])"
# -> Oracle AI Database 26ai Free
```

---

## Part 2 — See it work (with sample content)

Load a public YouTube channel so you can watch the whole pipeline before using your own data:

```bash
mkdir -p exports/youtube
./.venv/bin/yt-dlp --skip-download --dump-json \
  "https://www.youtube.com/@LindaVivah/videos" > exports/youtube/videos.jsonl
./.venv/bin/python scripts/youtube.py
```

✅ **Checkpoint** — semantic search (matches by *meaning*, no API key needed):

```bash
./.venv/bin/python -c "import sys; sys.path.insert(0,'oracle/agent'); import db, content; \
  [print(f\"{r['dist']:.3f}  {r['title']}\") for r in \
   content.search_content(db.connect(),'using AI in my workflow',k=3)]"
```

Then the research agent (add `ANTHROPIC_API_KEY=...` to `oracle/.env` first):

```bash
cd oracle/agent && ../../.venv/bin/python demo_research.py
```

---

## Part 3 — Make it yours (bring your own content)

This is the point — your content, in your database.

### Easiest: your own YouTube
Swap the channel URL in Part 2 for yours (`https://www.youtube.com/@YOURHANDLE/videos`),
re-run the two commands. Done.

### Other platforms (Instagram, LinkedIn, TikTok, X)
There's no reliable way to auto-scrape these (logins + anti-bot + terms of service). Two
honest options:

1. **Official data export (recommended).** Each platform lets you download your own data —
   complete, legal, includes media + metrics. See **[EXPORT_GUIDE.md](EXPORT_GUIDE.md)**.
2. **A third-party collector.** If you use an external tool to gather your posts, that's fine —
   this system doesn't care *how* you collected it.

### The one contract: get your content into `posts`
However you collect, the only thing this system needs is rows in the Oracle `posts` table.
`scripts/youtube.py` is the template — copy it for a new source and map that source's fields
to: `title`, `caption` (text/description), `url`, `published_at`, `views`, and the platform.
The embedding is generated automatically in-DB on insert. That's it — search and the agent
work over the new content immediately, no other changes.

> So the system is **collector-agnostic**: exports, APIs, or a scraper all funnel into the
> same `posts` model.

---

## Part 4 — Level it up (optional)

Two additions that make this a real "second brain" — and showcase more of Oracle 26ai.

### A self-improving knowledge wiki (JSON Relational Duality)
Beyond raw posts, an LLM can **compile** your content into synthesized, linked **topic pages** —
a knowledge layer that improves as you add content. It's the strongest Duality showcase here:
a page is *both* a JSON **document** *and* a **graph** of relationships (links + citations).

```bash
cd oracle/agent
../../.venv/bin/python wiki.py        # compile topic pages (needs ANTHROPIC_API_KEY)
../../.venv/bin/python demo_wiki.py   # a page as a Duality JSON doc + the link/citation graph
```

What it builds in Oracle:
- `wiki_pages` — the page (document + vector embedding)
- `page_links` — page → page cross-links (relational graph)
- `page_sources` — citations back to your `posts` (relational)
- `wiki_page_dv` — a **Duality view** serving a page as ONE JSON document, citations nested

So a single wiki page exercises **relational + JSON Relational Duality + AI Vector Search** at
once. Search and the research agent then draw on these synthesized pages automatically.

### Use your brain from Claude Desktop (MCP)
An MCP server exposes the brain to any MCP client (e.g. Claude Desktop) over a local stdio
connection — everything stays on your machine. Register it (Claude Desktop → **Settings →
Developer → Edit Config**), then restart Claude:

```json
{
  "mcpServers": {
    "content-brain": {
      "command": "<repo>/.venv/bin/python",
      "args": ["<repo>/oracle/agent/mcp_server.py"]
    }
  }
}
```

Now ask Claude things like *"search my brain for what I've covered on AI inference"* or *"show
my wiki topics."* Tools exposed: `search`, `fetch`, `wiki`, `topics`, `recent`, `ingest_note`.

---

## Next steps

- **More sources** — repeat Part 3 for each platform; everything lands in one place.
- **Go always-on** — lift the local database to Oracle Cloud (Always Free Autonomous) so the
  agent runs 24/7 and your data is backed up. *(Optional — local stays fully private.)*
- **Concepts** — how embeddings, JSON Relational Duality, and agent memory work:
  **[BUILD_WALKTHROUGH.md](BUILD_WALKTHROUGH.md)**.
