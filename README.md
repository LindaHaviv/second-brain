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

> **New here? Take the hands-on workshop → [docs/TUTORIAL.md](docs/TUTORIAL.md)** — Part I is the
> core labs (set up → see it work → your content); Part II is modular labs you pick for *your* use
> case (research base, team knowledge, creator brain, personal wiki). The sections below are the
> reference version.

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

The full picture — every layer, the four memory types, the self-improving loop, the privacy scope,
and the MCP surface — on one page:

![Full architecture: your sources flow through loaders (normalize, redact, classify, embed in-DB) into one Oracle AI Database 26ai holding content, a self-growing wiki, and four memory types, with hybrid search and enforced privacy; a research agent, a daily self-improve loop, and pluggable LLM/deploy options sit alongside; an MCP server exposes the brain to Claude, ChatGPT, open-source clients, and your apps](docs/images/architecture-full.png)

New to the concepts (embeddings, duality, agent memory, the agent loop)? The
**[TUTORIAL](docs/TUTORIAL.md)** teaches each one as you build it, and
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** shows how the pieces fit. To go deeper on **agent memory**,
Oracle has two great resources: the free **[Oracle × DeepLearning.AI "Agent Memory" course](https://www.deeplearning.ai/courses/agent-memory-building-memory-aware-agents)**
and the **[Oracle AI Developer Hub](https://github.com/oracle-devrel/oracle-ai-developer-hub)**
(workshops + notebooks from RAG to memory-augmented agents — start with its
[Agent Memory Workshop](https://github.com/oracle-devrel/oracle-ai-developer-hub/tree/main/workshops/agent_memory_workshop)).

## Why Oracle

One database holds your content, its embeddings, and the agent's memory — so a single query
can retrieve across all of it. Two features make it a strong fit here: **JSON Relational
Duality** lets a post be one clean JSON document to your app *and* normalized, joinable
relational data underneath (bidirectional, no ORM, no syncing two copies); and **in-database
embeddings** generate the vectors inside the engine, so your content never leaves it. AI
Vector Search ties it together for semantic retrieval.

And the free path is unusually generous: locally, the 26ai container is free; in the cloud,
the **Always Free tier** includes **two Autonomous AI Databases** (1 OCPU + 20 GB each — this
whole build fits in one), an Arm VM that can host the MCP server, 200 GB of block storage, and
10 TB/month of outbound transfer — permanently, not a 12-month trial.

> **Already keep a markdown vault?** This build keeps markdown at its core — `sources/` is the
> portable, canonical layer. The database adds what files alone can't: semantic search with the
> embeddings *and* the model in one indexed engine, privacy as an enforced `visibility` constraint
> on every read path (not a folder convention), wiki citations as foreign keys that stay consistent,
> and agent memory that consolidates transactionally while the MCP server, sync job, and agents
> read and write concurrently. Markdown where it shines (authoring, portability); a database where
> the work is (retrieval, relationships, memory, governance).

## Step 0 — Prerequisites

- macOS (this guide is Apple Silicon / `arm64`; on Intel, drop the `platform:` line in
  `oracle/docker-compose.yml`).
- [Homebrew](https://brew.sh), and — **only for the final "Converse" step** — an LLM: an
  **Anthropic API key** (default), an OpenAI key, or a **free local model via Ollama**
  (`LLM_PROVIDER` in `oracle/.env` — search itself needs no key at all).

## Step 1 — Quickstart: stand up the database (~15 min)

```bash
# 1. Container engine (headless — no Docker Desktop)
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60

# 2. Python env (3.12 — newer versions may lack wheels for oracledb/onnx)
brew install python@3.12
python3.12 -m venv .venv
./.venv/bin/pip install -r oracle/agent/requirements.txt yt-dlp

# 3. Config (the CHANGE_ME_* placeholders work for the local sandbox; set your own for anything real)
cp oracle/.env.example oracle/.env

# 4. Start Oracle AI Database 26ai (Free container image) locally
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

## Step 2 — Collect + search your content

```bash
# Collect: pull a public channel's metadata (any public channel works — swap in yours later)
mkdir -p exports/youtube
./.venv/bin/yt-dlp --skip-download --dump-json --playlist-items 1-7 \
  "https://www.youtube.com/@oracledevs/videos" > exports/youtube/videos.jsonl
./.venv/bin/python scripts/youtube.py

# Search: semantic search over what you just loaded (no API key needed)
./.venv/bin/python -c "import sys; sys.path.insert(0,'oracle/agent'); import db, content; \
  [print(f\"{r['dist']:.3f}  {r['title']}\") for r in \
   content.search_content(db.connect(),'protecting data in the cloud',k=3)]"
```

Swap in any channel, or ingest other platforms via their data exports — see
**[docs/EXPORT_GUIDE.md](docs/EXPORT_GUIDE.md)**. Every platform lands in the same `posts`
model, so the pipeline is the same.

## Step 3 — Converse: the research agent (needs an LLM)

```bash
# oracle/.env:  ANTHROPIC_API_KEY=sk-ant-...   (default)
#   — or LLM_PROVIDER=ollama for a free local model, LLM_PROVIDER=openai for OpenAI
cd oracle/agent && ../../.venv/bin/python demo_research.py
```

The agent searches your content, answers grounded in it (citing your videos), and records
each research run to `agent_memory`.

## Step 4 — Beyond the quickstart: make it a real second brain

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

![The same question answered in Claude and in ChatGPT by the same Second Brain connector — same brain, any chat](docs/images/mcp-search.png)

## How it all connects

The data flow, in one breath: **loaders** normalize any source into `posts` (embeddings are
generated in-database on insert, long content is chunked into passages). The **wiki compiler**
reads those posts and writes synthesized topic pages whose citations are foreign keys back to
them. **Agents** answer from posts + wiki and write what they learn to `agent_memory`;
**consolidation** distills those runs into durable `semantic_memory` facts. The **MCP server**
serves all of it to any AI client, and its write tools (`ingest_note`, `save_chat`) flow new
knowledge back in. Every read path filters `visibility = 'content'`, so private items are out of
search AND out of the self-improving loop.

## How it runs day to day

Once your content is in, one scheduled job keeps every derived layer current, in an order that
can never leak or go stale:

```
sync.py:  pick up new chat-export zips (ingest_exports.py watches your downloads folder)
          → pull API sources (Instagram, Notion) → Claude Code sessions (local, automatic)
          → classify private vs content (safety net re-runs after fresh imports)
          → refresh + GROW the wiki → consolidate memory
          → OAMP privacy sweep (auto-added on the package backend)
```

Run it by hand anytime (`./.venv/bin/python scripts/sync.py`) or schedule it daily — the
**[TUTORIAL](docs/TUTORIAL.md)** ships a ready macOS LaunchAgent plist. For chats there is also
real-time capture: say *"save this chat to my brain"* in any connected client and the `save_chat`
tool stores it on the spot, no export needed.

## How it improves itself

"Self-improving" here means two specific mechanisms — and, deliberately, not a third:

- **The knowledge self-organizes.** The wiki doesn't just refresh pages whose sources changed —
  it *proposes new pages* when enough content clusters around a topic no page covers yet. On the
  default backend the package extracts durable memories from every exchange as it happens, and
  nightly consolidation distills raw agent runs (`agent_memory`) into global facts
  (`semantic_memory`) — recall reads both. Nobody asks for any of it; the daily sync keeps it fed.
- **The agents compound experience.** Every run follows **recall → act → record**: an agent
  recalls consolidated facts before acting, does the work, and records the outcome. Tonight's
  runs become tomorrow's recalled facts — the research agent literally stops re-deriving things
  it worked out last month. The more you use it, the better it answers.
- **Nothing self-modifies.** No agent rewrites its own code or prompts, and no model decides what
  runs when — scheduling and routing stay deterministic (a 100-line `sync.py` and cron-style
  timers). The behavior improves because the *memory it stands on* grows, which means every
  improvement is inspectable with plain SQL: `SELECT * FROM semantic_memory ORDER BY created_at`.

The privacy filter is part of the loop, not an afterthought: consolidation and the wiki only read
`visibility = 'content'`, so a private item can never be laundered into a "learned" fact. Accuracy
is guarded the same way: a verification pass fact-checks every research answer against the run's
own evidence before it is returned or remembered, and consolidation refuses to promote unverified
claims into durable facts — so the loop compounds knowledge, not mistakes.

And because quality can regress silently, the repo ships **evals** alongside the tests (plain
Python + JSON golden sets, no framework): `tests/eval_retrieval.py` (golden queries that must keep
ranking — free, in-database), `eval_classifier.py` (privacy-classifier drift vs your reviewed
labels), `eval_verify.py` (a fabrication probe for the accuracy gate), `eval_grounding.py`
(do answers cite the sources they should?), and `eval_oamp.py` (seven probes for the memory
package — extraction smoke, privacy leak, isolation, enforcement — run on every package
upgrade). Tests prove the code runs; evals prove the system still finds and says the right
things.

## Step 5 — Take it to the cloud (optional)

Everything above runs free on your laptop. The same code runs on an **Always Free Autonomous AI
Database** so the brain is always-on, backed up, and reachable from anywhere:

1. **Move the database** — wallet + load the same ONNX model + one copy script (ships
   content-scope only by default): **[docs/CLOUD_MIGRATION.md](docs/CLOUD_MIGRATION.md)**.
2. **Host the MCP server** — one small container (the guide deploys to Fly.io) with OAuth + an
   email allowlist, so claude.ai, ChatGPT, and your phone can reach it:
   **[docs/HOSTED_MCP.md](docs/HOSTED_MCP.md)**.

## Repo layout

```
oracle/            the database: docker-compose, schema (Duality + 4 memory types + wiki),
                   setup SQL; the agents (db / content / memory / oamp_memory / research_agent /
                   idea_agent / wiki) + the MCP server (mcp_server stdio, mcp_http hosted)
scripts/           loaders (youtube, notion, instagram, instagram_export, chatgpt, claude_chats,
                   linkedin, linkedin_harvest, obsidian, gdrive) + pipeline (classify_private, sync, consolidate,
                   oamp_sweep) + ops (apply_schema, load_model_cloud, copy_local_to_cloud,
                   lint_wiki, review)
tests/             regression suite (test_brain.py) + quality evals (eval_retrieval /
                   eval_classifier / eval_verify / eval_grounding / eval_oamp, with golden sets)
deploy/            hosted-MCP container (Dockerfile; fly.toml lives at repo root)
sources/           canonical content as Markdown + frontmatter (source of truth)
docs/              TUTORIAL (start here) · BLOG · ARCHITECTURE · EXPORT_GUIDE ·
                   CLOUD_MIGRATION · HOSTED_MCP
```

`sources/` is the canonical layer; the database is a derived, rebuildable view of it.

## What's included

- [x] Collect → Store → Search → Converse — self-improving research agent over your content
- [x] **Many sources, one model** — YouTube (+ transcripts), Notion, **Instagram** (API *or*
  export — captions + reel transcripts), **LinkedIn**, **ChatGPT/Claude** exports — all into one
  `posts` table (`scripts/`)
- [x] **Four agent-memory types, two ways** — episodic, semantic, conversational,
  procedural. The default is Oracle's official
  [AI Agent Memory package](https://docs.oracle.com/en/database/oracle/agent-memory/)
  (`oracleagentmemory` — maintained + benchmarked, auto-extraction, hybrid retrieval, the
  privacy guard as custom extraction instructions **plus a structural deny-list sweep that
  enforces it**, and global consolidated facts merged into recall; `oamp_memory.py`).
  `MEMORY_BACKEND=custom` switches to the **learning track**: the same layer hand-built as
  tables you can read with SQL (how Oracle's DeepLearning.AI course teaches it) — and the
  **fully-local path**, auto-selected when you configure Ollama. Episodic + procedural are
  this build's extensions of the core on both (+ a
  [LangGraph example](examples/langgraph_oamp.py), + `tests/eval_oamp.py` — 7 probes to run
  on every package upgrade)
- [x] **Knowledge wiki layer** — LLM-compiled, self-improving topic pages (`wiki.py`) + a Duality
  view; the strongest relational + JSON + vector showcase
- [x] **Hybrid search** — vector + keyword (Reciprocal Rank Fusion)
- [x] **Content series (your own)** — group posts into a named `series` (a tutorial series, an
  interview show, book notes, a weekly update…) via a Notion `Series` label or `classify_series.py`;
  surfaced by a `by_series` tool + flagged in search — **define whatever series fit your content**
- [x] **Private by scope** — a `visibility` flag keeps financials/private items out of search **and**
  the self-improving loop; `classify_private.py` tags private + off-topic items on ingest
- [x] **Self-improving sync** — `sync.py` (pull → wiki refresh → consolidate) on a daily schedule,
  so the derived layers never go stale
- [x] **Idea & repurposing agent** — grounded next-content suggestions (`idea_agent.py`)
- [x] **MCP server, everywhere** — local (stdio) **+ hosted** (HTTP + WorkOS OAuth + allowlist),
  reachable from **claude.ai, ChatGPT, and your phone**; read tools annotated read-only, the write
  tool gated (`MCP_READONLY`) — see [docs/HOSTED_MCP.md](docs/HOSTED_MCP.md).
  (This build uses the **custom, portable, Python** route — full control, works with the local
  container, portable to any database. Oracle's fully-managed
  [Autonomous AI Database MCP Server](https://www.oracle.com/autonomous-database/mcp-server/)
  — zero-ops + DB-identity governance, built into Autonomous AI Database — is the official
  managed alternative when your brain lives there.)
- [x] **Cloud** — lift to Oracle Autonomous AI Database ([docs/CLOUD_MIGRATION.md](docs/CLOUD_MIGRATION.md))
- [x] **Maintenance** — `lint_wiki.py` (review candidates) + `review.py` (leaked-secret scan)
- [ ] Roadmap — live Instagram performance metrics via API sync · a lightweight UI

## Make it yours (safely)

Forking this into *your* second brain is the point — these five steps keep yours private while
you do:

1. **Change every demo password** (`oracle/.env`) — the defaults are public in this repo.
2. **Decide your private categories first**, then ingest — adapt `classify_private.py`'s rubric to
   *your* private material (don't publish what those categories are), and run it after every import.
3. **Your own auth, before hosting** — your own WorkOS allowlist (`ALLOWED_SUBS`) or bearer token.
   The server refuses to start unconfigured, so there's no accidental open door.
4. **Never commit** `.env`, the cloud wallet, `exports/`, or `sources/` — already gitignored; keep
   it that way, and run `scripts/review.py` before sharing anything derived from your data.
5. **Customize the personal bits** — your sources, your `series` labels, your wiki topics. The code
   is generic; everything "you" lives in env vars and your data.
6. **Personal agents go in a private companion repo** — make the gitignored `private/` dir its own
   private git repo for agents that encode *your* workflow (they can import the public engine
   directly). Publish patterns; keep personalization private.

## Notes

- **LLM-agnostic** — Claude here, but the agent talks to any LLM; the database is the constant.
- **Local-first, cloud-optional** — runs entirely on your machine; your data stays put — and the
  cloud copy script ships **only the content scope** by default.
- Oracle AI Database Free in Docker is the same engine as the cloud — features used here
  (AI Vector Search, JSON Relational Duality, in-DB ONNX embeddings) work in both.
