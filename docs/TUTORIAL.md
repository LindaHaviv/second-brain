# Tutorial — from zero to a self-improving research agent over your own content

A guided walkthrough. By the end you'll have a content store, semantic search over it, a Claude
research agent that answers grounded in *your* content and remembers what it found, a compiled
knowledge wiki, private data kept private, a daily job that keeps it all current, and access from
any AI client — starting **locally on your machine**, cloud optional.

The path, in order:
**set up → see it work → make it yours → keep private data private → compile a wiki → keep it
current → use it everywhere → go always-on.**

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

![The research agent answering from your own content, citing your videos, and recording the run to agent_memory](images/agent-answer.png)

---

## Part 3 — Make it yours (bring your own content)

This is the point — your content, in your database. The system is **collector-agnostic**: the only
thing it needs is rows in the Oracle `posts` table. This repo ships loaders you can use directly:

| Source | How | Loader |
|---|---|---|
| **YouTube** | public metadata (yt-dlp) + transcripts | `scripts/youtube.py`, `youtube_transcripts.py` |
| **Notion** | API (pages/databases) | `scripts/notion.py` |
| **Instagram** | official **API** (creator/business) *or* data export | `scripts/instagram.py`, `instagram_export.py` |
| **ChatGPT / Claude** | data export (JSON) | `scripts/chatgpt.py`, `claude_chats.py` |
| **LinkedIn** | data export or captured posts | `scripts/linkedin.py` |

**Don't scrape** the social platforms (logins + anti-bot + terms of service = account risk). Use
each platform's **official API or data export** — complete, legal, and it includes media/metrics.
See **[EXPORT_GUIDE.md](EXPORT_GUIDE.md)** for exactly where to click for each one.

> **📸 Tip — capture what you *said*, not just what you posted.** For video, pull **transcripts**
> (YouTube captions, or the `.srt` files in an Instagram export). The brain searches text, so a
> transcript makes the *content of a video* findable — not just its caption.

**The one contract:** map any source's fields to `title`, `caption` (text), `url`, `published_at`,
and the platform, then insert into `posts`. The embedding is generated in-DB automatically. Copy
any loader above as a template — search and the agent work over the new content immediately.

---

## Part 4 — Keep private data private (do this before you host it)

Your sources mix things you want an assistant to surface with things you **don't** (financials,
contracts, private notes — only you know which). Two mechanisms keep them apart:

- **A `visibility` scope on every item** — `content` (default) vs a **private** value. Every search,
  the wiki, and memory consolidation filter to `visibility='content'`, so private items are excluded
  from retrieval **and** from the self-improving loop (so it can't quietly re-derive them).
- **A classify-on-ingest pass.** After importing chats, run the classifier — it labels each item
  and tags the private / off-topic ones so they never reach the content brain:

```bash
./.venv/bin/python scripts/classify_private.py            # preview
./.venv/bin/python scripts/classify_private.py --apply    # tag private + off-topic
```

> **🔒 Teach the pattern, not your secrets.** Decide *your* private categories and keep them in a
> separate scope (or local-only, never on a hosted server). Don't publish exactly what you keep
> private or where — that's a map for anyone trying to reach it. Full guidance:
> **[SECURITY.md](../SECURITY.md)**.

---

## Part 5 — Compile a self-improving knowledge wiki (JSON Relational Duality)

Beyond raw posts, an LLM can **compile** your content into synthesized, linked **topic pages** — a
knowledge layer that improves as you add content. It's the strongest Duality showcase here: a page
is *both* a JSON **document** *and* a **graph** of relationships (links + citations).

```bash
cd oracle/agent
../../.venv/bin/python wiki.py            # compile topic pages (needs ANTHROPIC_API_KEY)
../../.venv/bin/python wiki.py --refresh  # incremental: only recompile what new content changed
../../.venv/bin/python demo_wiki.py       # a page as a Duality JSON doc + the link/citation graph
```

What it builds in Oracle: `wiki_pages` (document + vector embedding), `page_links` (page→page graph),
`page_sources` (citations back to your `posts`), and `wiki_page_dv` (a **Duality view** serving a
page as ONE JSON document with citations nested). One page exercises **relational + JSON Relational
Duality + AI Vector Search** at once.

![A compiled wiki page: synthesized overview on the left, its nested citations and cross-links on the right](images/wiki-page.png)

---

## Part 6 — Keep it current (the self-improving loop)

New content is only useful if the *derived* layers keep up. The rule: **whenever content lands,
refresh the wiki and consolidate memory.** `scripts/sync.py` encodes that order:

```
pull configured API sources  →  wiki refresh  →  consolidate memory
```

```bash
./.venv/bin/python scripts/sync.py
```

**Schedule it (macOS LaunchAgent)** — a `~/Library/LaunchAgents/com.you.secondbrain.sync.plist`
that runs `scripts/sync.py` daily, then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.you.secondbrain.sync.plist
launchctl list | grep secondbrain     # confirm it's registered
```

(A LaunchAgent only fires while your Mac is awake; a missed run fires on next wake.) Consolidation
distills your research runs into durable **semantic facts**, so the agent stops re-deriving your
themes every time — it gets sharper the more you use it. Export-only sources (ChatGPT/LinkedIn) are
a two-step manual flow: drop the export, run `classify_private.py`, then `sync.py` folds it in.

---

## Part 7 — Use your brain everywhere (MCP)

An **MCP server** exposes the brain to any MCP client. Start local (stdio — everything stays on your
machine); register it in Claude Desktop (**Settings → Developer → Edit Config**), then restart Claude:

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

Now ask Claude *"search my brain for what I've covered on AI inference"* or *"show my wiki topics."*
Tools: `search`, `fetch`, `wiki`, `topics`, `recent`, `ingest_note` — with the read tools annotated
`readOnlyHint` and the one write tool gated, so clients can auto-allow reads and ask before writes.

![Asking Claude to search your brain — it calls the connector and answers from your own content](images/mcp-search.png)

**Want it on your phone / in ChatGPT too?** Host the same server (HTTP + OAuth + an allowlist) so
it's reachable from claude.ai and ChatGPT — see **[HOSTED_MCP.md](HOSTED_MCP.md)**. Lock it down
first (auth on every request, allowlist, `MCP_READONLY` if it shouldn't accept writes).

> **Build or managed.** This is the **custom** route (Python tools, full control, web/mobile
> connector reach, portable to any database, runs on **any tier including Always Free**) — the fit
> for this build. Oracle also offers a fully **managed**
> [Autonomous AI Database MCP Server](https://www.oracle.com/autonomous-database/mcp-server/) (Select
> AI Agent PL/SQL tools, DB-identity governance) — a **paid-instance** feature for zero-ops + DB-level
> governance once you're on paid infrastructure.

---

## Part 8 — Go always-on (optional cloud)

When you want it backed up and running 24/7, lift the local database to **Oracle Autonomous Database**
(Always Free) — same engine, managed, no code changes (the app connects over a wallet). See
**[CLOUD_MIGRATION.md](CLOUD_MIGRATION.md)**. Local stays fully private if you'd rather not.

---

## Next steps

- **More sources** — repeat Part 3 for each platform; everything lands in one `posts` model.
- **Concepts** — how embeddings, JSON Relational Duality, and agent memory work:
  **[BUILD_WALKTHROUGH.md](BUILD_WALKTHROUGH.md)**.
- **Security** — before you host anything, walk **[SECURITY.md](../SECURITY.md)**.
