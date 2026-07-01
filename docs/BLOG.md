# Build a Second Brain on Oracle AI Database 26ai

*A step-by-step build: a self-improving research agent grounded in **your own sources** — the data,
its embeddings, and the agent's memory all living in one database.*

---

Everything you make or collect scatters — across apps that each remember a little and forget the
rest. Your notes live in one place, your research in another, the long AI chats where the real
thinking happened in a third, and the assistant you ask for help has no idea about any of them.

This walkthrough builds the fix: a **second brain** — one place that holds *your* stuff, searchable
by *meaning*, with an agent that researches on top of it and **remembers what it learned**. We'll
build it end to end and run every step, starting on a **public sample** so you watch the whole thing
work, then point it at your own sources.

**You choose the sources** — Notion, your videos, your AI chats, docs, bookmarks, whatever matters
for your use case (content, research, work knowledge, a personal wiki). The build is the same; only
the sources change.

We'll build it on **Oracle AI Database 26ai**, and the reason that matters is simple: one engine does
relational data, JSON documents, **AI Vector Search**, and even runs the embedding model **inside
the database** — so there's far less glue code, and your data, its meaning, and the agent's memory
all live together.

By the end you'll have more than an agent — you'll have a **foundation**: a research agent you can
ask *"what do I know about X, and what's new this week?"* that gets sharper every time you use it,
an **MCP server** that plugs that same brain into Claude, ChatGPT, and whatever comes next, and a
memory layer that every *future* agent you build gets to share. The research agent is just the
first thing you put on top of it.

![Second Brain architecture: your chosen sources → one Oracle 26ai database (content + wiki + memory) → a research agent + an MCP server any AI client can call](images/architecture.svg)

**Who this is for / what you need:** any developer comfortable with a terminal — **no prior Oracle
experience required.** You'll need a Mac with [Homebrew](https://brew.sh), Python 3.12, and ~20
minutes. Everything runs **locally and headless** (no Docker Desktop, no cloud account); going to
Oracle Cloud is an optional last step.

> **Follow along here, grab the full code there.** This article is self-contained — you can build it
> straight from these steps. The complete, runnable project (every loader, the agent, the MCP server)
> lives at **[github.com/LindaHaviv/second-brain](https://github.com/LindaHaviv/second-brain)**.

**What we'll build — three layers, one database:**
1. **Content** — everything you've made, as rows you can read back as JSON documents.
2. **A compiled wiki** — synthesized topic pages over that content (self-maintaining).
3. **Agent memory** — what the agent has learned, in all four flavors.

Here's the whole system on one page — the map for the steps that follow:

![Full architecture: sources → loaders → one Oracle 26ai database (content + wiki + 4 memory types) with hybrid retrieval and a privacy scope → a research agent and a self-improving daily sync → an MCP server reachable from any AI client; the LLM and embedding model are pluggable](images/architecture-full.svg)

Let's build each, one step at a time.

---

## Step 1 — Stand up Oracle 26ai locally (~15 min)

We run the free **Oracle AI Database 26ai** container image locally — the *same engine* as the
cloud, so AI Vector Search, JSON Relational Duality, and in-DB ONNX embeddings all work on your
machine. Clone the repo (it carries the
schema and setup scripts), then:

```bash
# container engine (headless — no Docker Desktop)
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60

# python env (3.12)
brew install python@3.12
python3.12 -m venv .venv
./.venv/bin/pip install -r oracle/agent/requirements.txt yt-dlp

# config (the CHANGE_ME_* placeholders work for the local sandbox)
cp oracle/.env.example oracle/.env

# start Oracle AI Database 26ai, then apply schema + load the embedding model
docker-compose -f oracle/docker-compose.yml up -d
./oracle/download-model.sh
./oracle/bootstrap.sh
```

**✅ Checkpoint** — confirm it's the real thing and ready:

```bash
./.venv/bin/python -c "import sys; sys.path.insert(0,'oracle/agent'); import db; \
  print(db.connect().cursor().execute( \
  \"select product from product_component_version where product like 'Oracle%'\").fetchone()[0])"
# -> Oracle AI Database 26ai ...   (the edition suffix varies by container image)
```

You now have a live database with the content schema, the Duality view, four memory tables, and the
`MINILM` embedding model loaded. Everything below runs against it.

---

## Step 2 — Store content, read it as documents (JSON Relational Duality)

Load a public YouTube channel as **sample data**, so you can watch the pipeline before using your own:

```bash
mkdir -p exports/youtube
# any public channel works — pick one to see it run, then swap in your own
./.venv/bin/yt-dlp --skip-download --dump-json \
  "https://www.youtube.com/@YOURHANDLE/videos" > exports/youtube/videos.jsonl
./.venv/bin/python scripts/youtube.py
```

Here's what that data lands in — and why the storage model is worth a minute of your attention.

**The problem JSON Relational Duality exists to solve** is one of the oldest tensions in building
apps. Your *application* thinks in documents: a post is one thing — its text, its platform, its
media — and you want to read and write it as one JSON object, the way your code already models it.
Your *database* wants normalized rows: platforms stored once (not duplicated into every post),
media in its own table, consistency enforced by the engine. For decades you had to pick a side —
a document store (great ergonomics, but data duplicated across documents, updates that fan out,
weak joins) or relational + an ORM (consistency, but a mapping layer, migration friction, and the
classic object-relational impedance mismatch). Some teams run *both* and sync them — now you have
two copies that drift.

**Duality's answer: don't pick.** You declare — once, in SQL — how the relational tables compose
into a document, and the database serves **both interfaces over the same rows**. Read the view,
you get one JSON document with everything nested. Write JSON *through* the view (insert, update,
delete), and the engine updates the underlying normalized tables. Change a row relationally, the
document reflects it instantly. Same data, two shapes, zero sync code — the "duality" is literal:

```sql
CREATE TABLE posts (
  post_id       NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  platform_id   VARCHAR2(20) NOT NULL,
  kind          VARCHAR2(20) DEFAULT 'post',     -- post | reel | video | article | tweet
  title         VARCHAR2(1000),
  caption       CLOB,
  content_embedding VECTOR(384, FLOAT32)         -- semantic search, in-DB (next step)
);

-- one "post" served as a document, its platform + media nested, fully read/write:
CREATE OR REPLACE JSON RELATIONAL DUALITY VIEW post_dv AS
  posts @insert @update @delete {
    _id : post_id   title : title   caption : caption   kind : kind
    platform : platforms { name : display_name }
    media    : media [ { url : url  kind : kind } ]
  };
```

Your app reads the view as JSON while the database keeps the relational tables consistent
underneath. And this matters *more* in the agent era, not less: **agents and MCP tools consume
JSON**, while governance still wants normalized, consistent truth — Duality serves both from one
table. It isn't decorative here either: in Step 5, every wiki read the agent makes goes through a
Duality view — one query returns the page *with its citations already nested*.

---

## Step 3 — Search by meaning (vectors *and* the model, in the database)

To search by meaning you need embeddings. With **26ai you don't call an external embedding API** —
you load a small ONNX model (MiniLM) *into the database* once (Step 1 did this), then generate
embeddings in SQL:

```sql
-- the model was loaded once, from a local file:
EXEC DBMS_VECTOR.LOAD_ONNX_MODEL('DATA_PUMP_DIR', 'all_MiniLM_L12_v2.onnx', 'MINILM', ...);
```

Now semantic search is just SQL — embed the query and rank by cosine distance, no keys, no data
leaving the database:

```sql
SELECT title, caption
FROM   posts
ORDER  BY VECTOR_DISTANCE(content_embedding,
                          VECTOR_EMBEDDING(MINILM USING :q AS DATA), COSINE)
FETCH FIRST 5 ROWS ONLY;
```

**✅ Checkpoint** — try it (no API key needed):

```bash
./.venv/bin/python -c "import sys; sys.path.insert(0,'oracle/agent'); import db, content; \
  [print(f\"{r['dist']:.3f}  {r['title']}\") for r in \
   content.search_content(db.connect(),'using AI in my workflow',k=3)]"
```

Two refinements the repo adds: it **chunks** long content (transcripts, chats) into a
`content_chunks` table so a query lands on the right *passage*, not just the right item; and it fuses
vector search with a **keyword** pass via Reciprocal Rank Fusion — so exact names, handles, and error
codes that pure-vector search can miss still surface.

> **⚡ 26ai also has this natively.** Oracle AI Database ships built-in **hybrid search** — a hybrid
> vector index (Oracle Text + vectors on one column) queried through `DBMS_HYBRID_VECTOR.SEARCH`.
> We hand-roll the fusion here because our retrieval spans **three tables** (posts + chunks + wiki
> pages, ranked together) and because seeing RRF explicitly is half the lesson — but for
> single-table hybrid search in production, reach for the native feature first.

---

## Step 4 — The research agent + four kinds of memory

Here's the agent you came for. Add your key to `oracle/.env` and run it:

```bash
# oracle/.env:  ANTHROPIC_API_KEY=sk-ant-...
cd oracle/agent && ../../.venv/bin/python demo_research.py
```

It's a small, transparent loop (Claude + tools) — the database does the heavy lifting. Its tools:
search your content, read a post, read a wiki page, search the live web. It grounds claims about
*your* work in *your* content and uses the web for what's current.

What makes it a **second brain** rather than a search box is **memory** — and we model all four types
the agent-memory literature talks about, each a table in the same database:

| Memory | Table | What it holds |
|---|---|---|
| **Episodic** | `agent_memory` | every past research run (question, outcome, sources, lesson) |
| **Semantic** | `semantic_memory` | durable facts distilled from those runs |
| **Conversational** | `conversations` | the current multi-turn context |
| **Procedural** | `procedural_memory` | the agent's tools, retrieved by relevance per question |

Before answering, the agent **recalls** relevant past runs and learned facts, and **ranks its own
toolset** against the question (procedural memory — with four tools it's a hint; at forty it's how
you'd pick which tools to send at all). After answering, it **records** the run. Periodically it
**consolidates** episodic memory into semantic facts —
distilling "what happened" into "what I now know about this creator." That's the self-improving loop:

```
 answer  →  record the run  →  recall + consolidate  →  answer better next time
```

The more you use it, the more it knows your themes, recurring questions, and gaps — and stops
re-deriving them every time. (In the repo this runs automatically every few research runs, plus a
daily scheduled consolidation.)

> **🔧 The LLM is a choice, not a dependency.** This build uses **Claude**, but only three pieces
> talk to an LLM at all — the agent loop, the wiki compiler, and memory consolidation — and each is
> a plain API call you can point at **OpenAI** or a **local open-source model** (e.g. Llama via
> [Ollama](https://ollama.com)). Everything else — the database, the schema, semantic search, the
> MCP server — is LLM-free: the embeddings are an **open-source MiniLM running inside Oracle**, so
> search needs **no API key at all** (you already proved that at the Step 3 checkpoint).

> **📦 Build it, or use Oracle's package — your choice.** This build makes the memory layer from
> scratch (four purpose-built tables) so you *see* how agent memory works, and get first-class
> **procedural** memory plus a scheduled consolidation loop. If you'd rather not hand-roll it, Oracle
> ships an official Python package — the **Oracle AI Agent Memory Package** (`oracleagentmemory`) —
> that turns the same database into a memory core, with framework adapters (OpenAI / Claude Agent SDK
> / LangGraph), multi-actor scoping, and a published LongMemEval benchmark. Pick by use case: **build**
> for full control and to learn the model; **the package** for a maintained, drop-in layer. Either
> way, the memory lives in your Oracle AI Database. See the
> [Oracle AI Agent Memory Package](https://docs.oracle.com/en/database/oracle/agent-memory/).
>
> **Go deeper on agent memory:** the free **[Oracle × DeepLearning.AI "Agent Memory" course](https://www.deeplearning.ai/courses/agent-memory-building-memory-aware-agents)**
> and the **[Oracle AI Developer Hub](https://github.com/oracle-devrel/oracle-ai-developer-hub)**
> (workshops + notebooks: RAG → agents → memory-augmented agents) are the best places to learn the
> concepts behind this build.

![The agent answering with citations to your own posts, then building on the previous turn as agent_memory grows](images/agent-answer.png)

---

## Step 5 — Compile a self-improving knowledge wiki

> Already loaded **your own** data instead of the sample? Do the privacy scoping in Step 7 first —
> the compiler (and memory consolidation) should only ever read your `content` scope.

RAG re-synthesizes your knowledge on every question. We add a layer that **compiles it once**:

```bash
cd oracle/agent && ../../.venv/bin/python wiki.py        # needs ANTHROPIC_API_KEY
```

An LLM reads your content and writes synthesized **topic pages** — cross-linked, citing the source
posts — that improve as you add content. And the refresh works in both directions: it recompiles
the pages your new content touches, and when new content clusters outside every existing topic it
**proposes and compiles new pages** — the knowledge base grows on its own. It's the strongest Oracle showcase in the build, because a
wiki page is *both* a document *and* a graph:

- `wiki_pages` — the page (a JSON document **+** a vector embedding)
- `page_links` — page → page cross-links (**relational** graph)
- `page_sources` — citations back to your `posts` (**relational**)
- `wiki_page_dv` — a **Duality view** serving a page as one JSON document with its citations nested

So a single page exercises **relational + JSON Relational Duality + AI Vector Search** at once, and
the agent answers from your *synthesized* knowledge, tracing every claim back to a real video or
note. And the Duality view isn't just for show — **every wiki read in the agent and the MCP server
goes through it**: one query on `wiki_page_dv` returns the page with its citations already nested,
replacing the two manual joins you'd otherwise write.

![A compiled wiki page — a synthesized topic overview with its citations back to your posts nested alongside](images/wiki-page.png)

---

## Step 6 — Use it from anywhere (MCP)

Finally, make the brain a tool any AI client can call. A small **MCP server** exposes the standard
`search`/`fetch` connector contract — the same shape **Claude *and* ChatGPT** expect — plus `wiki`,
`topics`, `recent`, `by_series`, `overview`, and `ingest_note`. Run it **locally over stdio** (Claude Desktop /
Claude Code) by registering it in your client config:

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

Then open Claude and ask *"search my brain for what I've covered on AI inference"* — it answers from
your own content. The tools are **capability-scoped**: read tools are annotated `readOnlyHint` so a
client can auto-allow them, while the one write tool is marked a write so the client asks first (and
`MCP_READONLY` drops it entirely).

> **🔎 Show your work (great for teaching).** Instead of a black-box list, each `search` result
> carries **how it was found** — `match` (wiki / post / passage), `rank`, `score`, and `found_by`
> (`semantic`, `keyword`, or both). Add `explain=true` and you also get a `search_info` block naming
> the method: *hybrid — in-DB MiniLM semantic vectors (cosine) fused with keyword search via
> Reciprocal Rank Fusion.* So a demo can *see* a result that matched by **both** meaning and exact
> keyword — the retrieval isn't hidden, it's on the page.

Want it on your phone and in ChatGPT? **Host** the same server over HTTP — but that puts your brain
on the public internet, so lock it down first (OAuth + an allowlist; see Security below) — and if
you've loaded your own data, do Step 7's privacy scoping **before** you host.

> **🔌 Two ways to serve it — and which fits here.** This build uses a **custom MCP server**
> (Python): you keep full control of the tools, it speaks the OAuth *custom-connector* flow that
> **claude.ai web/mobile and ChatGPT** use, it's **database-agnostic**, and it works with the
> **local container** — no cloud required. That's the right fit for a portable, teach-the-internals
> build. Oracle *also* offers a fully **managed** MCP server built into **Autonomous AI Database**
> (cloud): no infrastructure to run, tools defined as **Select AI Agent** (PL/SQL) tools, access
> **governed by database identity** with native auditing. If your brain lives in Autonomous AI
> Database and PL/SQL tools cover your needs, the managed server is the zero-ops path. We studied it
> and **borrowed its security best-practices** into this custom build — the prompt-injection guard
> and least-privilege DB user below. Docs:
> [Oracle Autonomous AI Database MCP Server](https://www.oracle.com/autonomous-database/mcp-server/).

![Claude calling the Second Brain connector and answering from your own content, the read tools auto-allowed and the write tool gated](images/mcp-search.png)

---

## Step 7 — Make it yours: your sources, kept private and current

You've watched the whole thing work on sample data. Now point it at *your* content — the system is
**collector-agnostic**, so the only thing it needs is rows in that `posts` table. Map any source's
fields to `title`, `caption` (the text), `url`, `published_at`, and the platform; the embedding is
generated in-DB on insert. The repo ships loaders for **Notion**, **YouTube** (+ transcripts), **Instagram** (API or
export — captions *and* reel transcripts), **LinkedIn**, and **AI chats** (Claude/ChatGPT exports),
and any one is a copyable template.

> **🎙️ Capture what you *said*, not just what you posted.** The brain searches text, so for video
> pull **transcripts** (YouTube captions; the `.srt` files in an Instagram export) — that makes the
> *content* of a video findable, not just its caption. **And don't scrape** the social platforms
> (logins + anti-bot + ToS = account risk); use each one's **official API or data export**.

**Private by scope.** Your sources mix public content with things you *don't* want surfaced
(financials, contracts — only you know which). Every item carries a `visibility` — `content` or a
private value — and *every* read path filters to `content`: search, the wiki compiler, and memory
consolidation. So private material is excluded from retrieval **and** from the self-improving loop —
which means the brain can't quietly re-derive it into "durable memory" after you've set it aside. A
classify-on-ingest pass tags private and off-topic items automatically.

**Current by loop.** New content is only useful if the *derived* layers keep up. One scheduled job
enforces the order **pull sources → classify → refresh the wiki → consolidate memory**, so your
synthesized pages and learned facts never go stale — and because they only ever read the `content`
scope, the auto-refresh stays safe. Each source type has its freshness path: **API sources**
(Instagram, Notion) are pulled automatically by the daily job; **public metadata** (YouTube) is a
loader re-run whenever you publish; **export-only sources** (ChatGPT/Claude/LinkedIn) have no push
API — set a monthly reminder to drop a fresh export; and **in-the-moment ideas** go in through the
MCP's `ingest_note` from any AI client. One rule to respect: re-importing a chat export resets
visibility tags, so classification must rerun before anything rebuilds — the sync job detects that
state and does it automatically as a safety net.

> **🏷️ Optional — group content into a *series*.** A `series` field lets you tag content into a
> named group you care about (an interview series, a tutorial series, a product line). Once tagged,
> search flags each result's series and a `by_series` tool lists it — so an assistant can answer
> *"list my interview episodes."* Define whatever series fit *your* content.

---

## Step 8 — Go always-on (optional cloud)

Everything above runs locally. When you want it always-on and backed up, lift it to **Oracle
Autonomous AI Database** — same engine, managed. The app connects over a wallet with **no code
changes**; you load the same ONNX model, copy the data, and you're running in the cloud. Local stays
fully private if you'd rather not — and the copy script ships **only the content scope** by default,
so your private data stays local even when the brain goes to the cloud.

---

## Security — don't skip this

Your brain holds *your* data, so treat it that way. The repo bakes these in; if you fork it, keep
them on:

- **Redact before you ingest.** AI-chat and coding transcripts leak API keys — scrub secret patterns
  *before* they hit the database. A `review.py` scans for anything that slipped through.
- **Keep private data separate — and out of the self-improving loop.** Scope each item, keep the
  private one **out of the searchable brain**, and make sure the parts that *self-improve* (memory
  consolidation, the wiki compiler) read **only** the content scope — otherwise the brain can quietly
  re-derive private facts back into "durable memory." Keep the most private data local and
  unadvertised, classify at ingest, re-check after each import. (Teach the pattern; don't publish
  exactly what *you* keep private.)
- **Never commit secrets.** `.env`, the cloud wallet, and your raw content are gitignored — keep them
  that way; keep real copies in a password manager, and rotate anything exposed.
- **Least privilege, no public database.** The app runs as a limited DB user (not admin), and the
  database is *never* exposed to the internet — only the MCP server talks to it.
- **Lock the front door if you host it.** A public MCP needs auth on *every* request. For
  claude.ai/ChatGPT that means **OAuth + an allowlist** — so even after a valid login, only *your*
  account is authorized; the server **refuses to start** with an empty allowlist.
- **Treat retrieved content as data, not instructions** (prompt-injection), and keep any
  write/update tools **human-approved**.

Full checklist: **[SECURITY.md](https://github.com/LindaHaviv/second-brain/blob/main/SECURITY.md)**.

---

## Make it yours (safely)

Replicating this as *your* second brain is the point — five steps keep yours private while you do:

1. **Change every demo password** before anything real touches the database.
2. **Decide your private categories first, then ingest** — adapt the classifier's rubric to *your*
   private material, run it after every import, and don't publish what your categories are (teach
   the pattern, not your specifics).
3. **Your own auth before hosting** — your own OAuth allowlist or bearer token. The server refuses
   to start with no auth configured, so there's no accidental open door.
4. **Never commit** secrets, the cloud wallet, or your raw exports — and scan derived content for
   leaked keys before sharing it.
5. **Customize the personal bits** — your sources, your series labels, your wiki topics. The code
   is generic; everything "you" lives in configuration and your data.

## This is just the beginning — you built a platform

Step back and look at what's actually here. It isn't one agent; it's **three layers that compound**:

- **The brain** — your content, its meaning, a synthesized wiki, and four kinds of memory, in one
  database. Every new source you add makes everything downstream smarter.
- **The MCP socket** — the brain as a *tool*, pluggable into Claude, ChatGPT, your phone, and any
  MCP client that ships next. Build the brain once; every AI surface you use can reach it.
- **The agents** — and here's the part that scales: **they share the brain.** The research agent is
  agent #1, and its self-improving loop (record → recall → consolidate) enriches the same memory
  every future agent reads. The repo ships agent #2 to prove the pattern: an **idea agent** that
  reads the consolidated facts (your themes, formats, *gaps*) and proposes what to make next —
  grounded in what you've actually made, about ~90 lines because the brain does the heavy lifting.

Your third agent is whatever your work needs — a meeting-prep briefer, a weekly digest writer, a
research assistant tuned to your domain. Each one is small, because the hard parts (retrieval,
memory, grounding, privacy scope) are already the platform's job. And each one makes the memory
richer for the rest.

## What you end up with

One database that holds your content, its meaning, your synthesized knowledge, and a growing memory
that every agent shares — with an MCP server that plugs it into any AI client, and a research agent
that improves with use as the first of many. Not a pile of notes you have to re-read: a foundation
that gets better the more you make *and* the more you build on it.

Clone it, point it at your own content, and ask it something only *you* would know the answer to.

> Full, runnable code + a step-by-step repo workshop:
> **[github.com/LindaHaviv/second-brain](https://github.com/LindaHaviv/second-brain)**.
