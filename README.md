# Content Control Center

Build a **self-improving research agent grounded in your own content**, on a single
Oracle database. Your content + its embeddings + the agent's memory all live in one engine;
the agent searches your content by meaning, answers grounded in it, and remembers what it
found. The LLM is pluggable (this build uses Claude); the database does the heavy lifting.

This repo is meant to be **followed step by step.** Everything runs locally and headless —
no Docker Desktop, no cloud account required. (Lifting it to Oracle Cloud is an optional
later step.)

> **New here? Start with the guided walkthrough → [docs/TUTORIAL.md](docs/TUTORIAL.md)**
> (set it up → see it work → make it yours). The sections below are the reference version.

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

## Repo layout

```
oracle/            the database: docker-compose, schema (Duality + agent memory),
                   setup SQL, the Claude agent (db / memory / content / research_agent)
scripts/           collectors/normalizers (export bundle -> sources/*.md + Oracle)
sources/           canonical content as Markdown + frontmatter (source of truth)
docs/              BUILD_WALKTHROUGH (concepts) · EXPORT_GUIDE (collecting data)
```

`sources/` is the canonical layer; the database is a derived, rebuildable view of it.

## Roadmap

- [x] Local: Collect → Store → Search → Converse (research agent over your content)
- [ ] Lift to **Oracle Cloud** (Always Free Autonomous) for always-on + durability (optional)
- [ ] Knowledge wiki layer (LLM-compiled) over `sources/`
- [ ] More sources (Instagram/LinkedIn/TikTok/X exports), a UI, brand-deal CRM

## Notes

- **LLM-agnostic** — Claude here, but the agent talks to any LLM; the database is the constant.
- **Local-first, cloud-optional** — runs entirely on your machine; your data stays put.
- Oracle Database Free in Docker is the same engine as the cloud — features used here
  (AI Vector Search, JSON Relational Duality, in-DB ONNX embeddings) work in both.
