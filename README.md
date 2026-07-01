# Second Brain

> *One private place for everything you've made and thought — your posts, videos, notes, and AI
> chats — unified in a single Oracle database: searchable by meaning, synthesized into a living
> wiki, reachable from any AI assistant, and yours alone.*

Everything you make and learn scatters across apps that each remember a little and forget the rest.
This is the fix: a **second brain** — one home for *your* content and thinking that you can **search
by meaning**, that **compiles itself into a synthesized knowledge layer**, that **keeps your private
data private**, and that you can **reach from Claude, ChatGPT, or your phone**. On top of it a
research agent answers grounded in your own work and gets sharper the more you use it.

Your content, its embeddings, and the agent's memory all live in one engine — the LLM is pluggable
(this build uses Claude); the database does the heavy lifting.

This repo is meant to be **followed step by step.** Everything runs locally and headless —
no Docker Desktop, no cloud account required. (Lifting it to Oracle Cloud is an optional
later step.)

> **New here? Start with the guided walkthrough → [docs/TUTORIAL.md](docs/TUTORIAL.md)**
> (set it up → see it work → make it yours). The sections below are the reference version.

> 🔒 **Using your real data, or putting it online? Read [SECURITY.md](SECURITY.md) first** —
> redaction, secrets handling, least-privilege DB users, and OAuth + an email allowlist for the
> hosted MCP.

## What you'll build

```
Collect  →  Store  →  Search  →  Converse
(yt-dlp)    (Oracle    (in-DB     (Claude research
            Duality)   vectors)    agent + memory)
```

- **Collect** — pull your content (this guide uses a public YouTube channel via `yt-dlp`).
- **Store** — normalize it into Oracle using a **JSON Relational Duality** model.
- **Search** — semantic search over your content with **in-database embeddings** (the model
  runs inside Oracle; your content never leaves it).
- **Converse** — a Claude agent that researches over your content and writes each run to an
  **agent-memory** table, so it improves over time.

New to the concepts (embeddings, duality, agent memory, the agent loop)? Read
**[docs/BUILD_WALKTHROUGH.md](docs/BUILD_WALKTHROUGH.md)** first.

## Why Oracle

One database holds your content, its embeddings, and the agent's memory — so a single query
can retrieve across all of it. Two features make it a strong fit here: **JSON Relational
Duality** lets a post be one clean JSON document to your app *and* normalized, joinable
relational data underneath (bidirectional, no ORM, no syncing two copies); and **in-database
embeddings** generate the vectors inside the engine, so your content never leaves it. AI
Vector Search ties it together for semantic retrieval.

## Prerequisites

- macOS (this guide is Apple Silicon / `arm64`; on Intel, drop the `platform:` line in
  `oracle/docker-compose.yml`).
- [Homebrew](https://brew.sh), and an **Anthropic API key** (only for the final "Converse" step).

## Quickstart

```bash
# 1. Container engine (headless — no Docker Desktop)
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60

# 2. Python env (3.12 — newer versions may lack wheels for oracledb/onnx)
brew install python@3.12
python3.12 -m venv .venv
./.venv/bin/pip install -r oracle/agent/requirements.txt yt-dlp

# 3. Config (demo passwords are fine for local; change for anything real)
cp oracle/.env.example oracle/.env

# 4. Start Oracle (Oracle Database Free 23.26.2, branded "26ai") in a container
docker-compose -f oracle/docker-compose.yml up -d

# 5. Download the in-DB embedding model, then bootstrap the database
#    (waits for healthy, applies schema, loads the ONNX model)
./oracle/download-model.sh
./oracle/bootstrap.sh
```

That gives you a live Oracle 26ai with the content schema, the Duality view, the
`agent_memory` table, and the `MINILM` embedding model loaded. Verify:

```bash
./.venv/bin/python -c "import sys; sys.path.insert(0,'oracle/agent'); import db; \
  print(db.connect().cursor().execute(\"select count(*) from user_objects\").fetchone())"
```

## Collect + search your content

```bash
# Collect: pull a channel's public metadata -> sources/youtube/*.md + Oracle
./.venv/bin/yt-dlp --skip-download --dump-json \
  "https://www.youtube.com/@LindaVivah/videos" > exports/youtube/videos.jsonl
./.venv/bin/python scripts/youtube.py

# Search: semantic search over what you just loaded (no API key needed)
./.venv/bin/python -c "import sys; sys.path.insert(0,'oracle/agent'); import db, content; \
  [print(f\"{r['dist']:.3f}  {r['title']}\") for r in \
   content.search_content(db.connect(),'using AI in my workflow',k=3)]"
```

Swap in any channel, or ingest other platforms via their data exports — see
**[docs/EXPORT_GUIDE.md](docs/EXPORT_GUIDE.md)**. Every platform lands in the same `posts`
model, so the pipeline is the same.

## Converse — the research agent (needs your API key)

```bash
# add your key to oracle/.env:  ANTHROPIC_API_KEY=sk-ant-...
cd oracle/agent && ../../.venv/bin/python demo_research.py
```

The agent searches your content, answers grounded in it (citing your videos), and records
each research run to `agent_memory`.

## Beyond the quickstart

Once the basics work, this scales into a real second brain — the full path is in
**[docs/TUTORIAL.md](docs/TUTORIAL.md)**:

- **Bring all your content** — Instagram, LinkedIn, ChatGPT/Claude exports, Notion, all into the
  same `posts` model ([docs/EXPORT_GUIDE.md](docs/EXPORT_GUIDE.md)). For video, pull **transcripts**
  so what you *said* is searchable, not just the caption.
- **Keep private data private** — a `visibility` scope + `classify_private.py` keep financials and
  private items out of search **and** the self-improving loop ([SECURITY.md](SECURITY.md)).
- **Keep it current** — `sync.py` (pull → wiki refresh → consolidate) on a daily schedule.
- **Use it everywhere** — host the MCP (OAuth + allowlist) and reach your brain from **claude.ai,
  ChatGPT, and your phone** ([docs/HOSTED_MCP.md](docs/HOSTED_MCP.md)).

![Asking Claude to search your Second Brain — it calls the connector and answers from your own content, read tools auto-allowed and the write tool gated](docs/images/mcp-search.png)

## Repo layout

```
oracle/            the database: docker-compose, schema (Duality + 4 memory types + wiki),
                   setup SQL; the agents (db / content / memory / research_agent / idea_agent /
                   wiki) + the MCP server (mcp_server stdio, mcp_http hosted)
scripts/           loaders (youtube, notion, instagram, instagram_export, chatgpt, claude_chats,
                   linkedin) + pipeline (classify_private, sync, consolidate, wiki) + ops
                   (apply_schema, load_model_cloud, copy_local_to_cloud, lint_wiki, review)
deploy/            hosted-MCP container (Dockerfile + fly.toml)
sources/           canonical content as Markdown + frontmatter (source of truth)
docs/              TUTORIAL (start here) · BLOG · BUILD_WALKTHROUGH · EXPORT_GUIDE ·
                   ARCHITECTURE · CLOUD_MIGRATION · HOSTED_MCP
```

`sources/` is the canonical layer; the database is a derived, rebuildable view of it.

## What's included

- [x] Collect → Store → Search → Converse — self-improving research agent over your content
- [x] **Many sources, one model** — YouTube (+ transcripts), Notion, **Instagram** (API *or*
  export — captions + reel transcripts), **LinkedIn**, **ChatGPT/Claude** exports — all into one
  `posts` table (`scripts/`)
- [x] **All four agent-memory types** — episodic, semantic (auto-consolidated), conversational, procedural
  (built from scratch to show the model; Oracle's official [OAMP package](https://docs.oracle.com/en/database/oracle/agent-memory/) is a drop-in alternative)
- [x] **Knowledge wiki layer** — LLM-compiled, self-improving topic pages (`wiki.py`) + a Duality
  view; the strongest relational + JSON + vector showcase
- [x] **Hybrid search** — vector + keyword (Reciprocal Rank Fusion)
- [x] **Private by scope** — a `visibility` flag keeps financials/private items out of search **and**
  the self-improving loop; `classify_private.py` tags private + off-topic items on ingest
- [x] **Self-improving sync** — `sync.py` (pull → wiki refresh → consolidate) on a daily schedule,
  so the derived layers never go stale
- [x] **Idea & repurposing agent** — grounded next-content suggestions (`idea_agent.py`)
- [x] **MCP server, everywhere** — local (stdio) **+ hosted** (HTTP + WorkOS OAuth + allowlist),
  reachable from **claude.ai, ChatGPT, and your phone**; read tools annotated read-only, the write
  tool gated (`MCP_READONLY`) — see [docs/HOSTED_MCP.md](docs/HOSTED_MCP.md).
  (Or use Oracle's fully-managed [Autonomous AI Database MCP Server](https://www.oracle.com/autonomous-database/mcp-server/)
  — zero-ops + DB-identity governance; this build is the custom, portable, Python route.)
- [x] **Cloud** — lift to Oracle Autonomous Database ([docs/CLOUD_MIGRATION.md](docs/CLOUD_MIGRATION.md))
- [x] **Maintenance** — `lint_wiki.py` (review candidates) + `review.py` (leaked-secret scan)
- [ ] Roadmap — live Instagram performance metrics via API sync · a lightweight UI

## Notes

- **LLM-agnostic** — Claude here, but the agent talks to any LLM; the database is the constant.
- **Local-first, cloud-optional** — runs entirely on your machine; your data stays put.
- Oracle Database Free in Docker is the same engine as the cloud — features used here
  (AI Vector Search, JSON Relational Duality, in-DB ONNX embeddings) work in both.
