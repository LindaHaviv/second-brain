# Build Walkthrough — teach-it-yourself guide

A concept-by-concept tour of how this project is built and *why*. Written so you can
re-teach it. Each section has: **the idea → why it matters → where it is in the code →
the one-liner to say out loud.**

Read top to bottom — each concept builds on the last.

---

## 0. The mental model (say this first)

> "I'm building one brain for my content business. Three layers: the raw stuff I've
> made, a knowledge base that organizes itself, and a database that makes it searchable
> and lets an AI agent get better over time."

| Layer | What | Format | Rebuildable? |
|---|---|---|---|
| 1. **Canonical** | every post/episode, raw | Markdown + YAML frontmatter (`sources/`) | it's the source of truth |
| 2. **Wiki** | LLM-compiled knowledge base (Karpathy's pattern) | Markdown in an Obsidian vault (`wiki/`) | yes — from layer 1 |
| 3. **Oracle 26ai** | query + agent memory | relational + vector (`oracle/`) | yes — from layer 1 |

**The rule that makes the whole thing safe:** layer 1 is *canonical and dumb*; layers 2
and 3 are *derived*. If a tool dies, you rebuild the derived layer. You never lose data
to a vendor.

> One-liner: *"Source of truth stays in plain text. Everything else is a rebuildable view on top."*

---

## 1. Why a database at all — and why this one

The agent needs four things from storage:

1. **Durable** — survives restarts (a goldfish that forgets isn't an agent).
2. **Queryable two ways** — by *meaning* (vector search) AND by *exact fields* (SQL).
3. **Structured** — track which actions succeeded/failed.
4. **Auditable** — you can inspect what the agent "learned."

A pile of JSON files gives you none of these. A vector-only DB gives you #2-by-meaning
but not the relational side. **Oracle 26ai does relational + JSON + vector in one
engine** — that's the consolidation story, and it's why one database covers the whole demo.

> One-liner: *"Memory has to be durable, searchable by meaning AND by SQL, and auditable. One engine that does all four is the win."*

---

## 2. JSON Relational Duality (the content model)

**File:** `oracle/schema/01_content_duality.sql`

The classic dilemma: model data as **relational tables** (clean, joinable, great for a
CRM of brands/deals) *or* as **JSON documents** (flexible, great for an app to read a
whole "post" at once)? Duality says: **don't choose.**

- You store data **normalized** in tables (`posts`, `brands`, `deals`, `media`).
- You define a **Duality View** that serves a "post" as one JSON document — caption +
  metrics + nested media + brand — and it's **read *and* write** through the view.

```sql
CREATE OR REPLACE JSON RELATIONAL DUALITY VIEW post_dv AS
  posts @insert @update @delete {
    _id: post_id, caption: caption,
    metrics: { likes, comments, views },
    media: media @insert @update @delete [ { mediaId: media_id, kind, url } ]
  };
```

No ORM, no "write to the table AND the document and keep them in sync." Oracle keeps them
in sync because they're *the same data*.

> One-liner: *"Store it relational for integrity; read and write it as JSON for the app. Same data, no sync code."*

---

## 3. Embeddings & vector search (the foundation of everything "by meaning")

This is the concept everything else rests on — teach it slowly.

**An embedding turns text into a list of numbers (a vector) that represents its
*meaning*.** Texts with similar meaning get vectors that point in similar directions.
"Creator burnout" and "influencers quitting" land near each other even though they share
no words.

- Our model (**MiniLM**) outputs **384 numbers** per text → `VECTOR(384, FLOAT32)`.
- **Closeness** is measured by **cosine distance** — smaller = more similar in meaning.
- To search millions of vectors fast, you build a **vector index** (HNSW = an
  approximate-nearest-neighbour graph). "Approximate" trades a sliver of accuracy for
  huge speed; we ask for 95% accuracy.

**In-database embeddings (the talking point):** Oracle runs the MiniLM ONNX model
*itself*. You call `VECTOR_EMBEDDING(MINILM USING 'some text' AS DATA)` in SQL and get a
vector back — **no external embedding API, data never leaves the database.**

Where it lives:
- `VECTOR(384, FLOAT32)` column — `schema/02_agent_memory.sql` (the optional at-scale
  `CREATE VECTOR INDEX` lives in `oracle/setup/03_vector_index.sql`)
- `VECTOR_EMBEDDING(...)` to make vectors, `VECTOR_DISTANCE(...)` to compare — `agent/memory.py`, `setup/02_retrieval_examples.sql`
- Loading the model once — `setup/01_load_onnx_model.sql`

> One-liner: *"An embedding is meaning as coordinates. Similar ideas sit close together, and the database can find the nearest ones — and it generates the embeddings itself, no outside API."*

---

## 4. Agent memory (the hero concept)

**File:** `oracle/schema/02_agent_memory.sql` + `oracle/agent/memory.py`

**The thesis: a model's context window is *working memory* — short-term, gone when the
session ends. Real agent memory is *persistent and queryable*.** Most agents are
goldfish; we give ours a hippocampus.

Three kinds of memory (teach the taxonomy — devs love it):
- **Episodic** — what happened in past runs (actions + outcomes). ← *what we build*
- **Semantic** — facts/knowledge (this is the Karpathy wiki, layer 2).
- **Procedural** — learned how-to patterns.

Our `agent_memory` table is **episodic memory done as a hybrid row**:

```
run_id, task, action, tool, outcome('success'|'failure'), reward(0..1),
detail(the lesson), embedding VECTOR(384), created_at
```

The trick is **two retrieval paths over the same row**:
- **Vector** (`embedding`) → "what past experiences are *relevant* to what I'm about to do?"
- **Relational** (`outcome`, `reward`, `tool`) → "what actually *worked*?" via plain SQL
  (`tool_stats` view aggregates success rate per tool).

That hybrid is the reason a database — not a flat file or a vector-only store — is the
right home for memory.

> One-liner: *"The context window is short-term memory. This table is long-term memory — searchable by meaning with vectors, and auditable by outcome with SQL."*

---

## 5. How an LLM agent uses tools (the mechanics)

Before the loop makes sense, the tool-use cycle:

1. You give the model a **tool definition** — name, description, JSON input schema.
2. The model, mid-response, emits a **`tool_use`** block ("call `publish_hook` with this input").
3. **Your code executes** the tool and sends back a **`tool_result`**.
4. The model continues; you **loop** until it stops asking for tools (`stop_reason: "end_turn"`).

The model never runs your code — it *requests* calls; your harness runs them. That's the
whole security boundary, and it's why *you* get to insert memory reads/writes around each step.

> One-liner: *"The model can't run code — it asks me to. So I control what happens before and after every tool call. That's where memory plugs in."*

---

## 6. The agent loop (where it comes together)

**File:** `oracle/agent/agent.py`

We use a **manual** loop (not the SDK's auto tool-runner) **on purpose**, because the
demo *is* the read/write around each step:

```
run_task(topic):
    task = "Write a high-engagement hook about: <topic>"
    memories = recall(task)          # 1. READ memory (vector search) BEFORE acting
    inject lessons into the prompt
    loop:
        resp = Claude.messages.create(... tools=[publish_hook] ...)
        if no tool call: stop
        for each publish_hook call:
            score = score_hook(hook)         # 2. ACT (the tool runs)
            record(task, hook, outcome, ...) # 3. WRITE memory (embedded in-DB)
            return score as tool_result
```

Model/config choices and the why:
- **`claude-opus-4-8`** — current most-capable model.
- **Adaptive thinking** (`thinking: {type: "adaptive"}`) — the model decides how much to
  reason per step; we preserve its thinking blocks when we loop (required for tool use).
- **We append the model's full `content`** back into the conversation each turn — that
  keeps the reasoning + tool-use blocks intact.

> One-liner: *"Recall before acting, record after acting. Same task next time, the recall surfaces what worked."*

---

## 7. The honest "self-improving" (don't fake this)

**File:** `oracle/agent/tools.py`

`publish_hook` scores a hook against a **latent rubric** (has a number? a question?
speaks to "you"? punchy?). **The agent never sees the rubric.** It only sees, in memory,
that past hooks with certain features scored higher. So on run #2 it writes a better hook
**because it recalled outcomes — not because we told it the answer.**

That's the difference between a real agent-memory demo and a scripted one. On camera,
name the hard parts so you're credible:
- **Relevance** — bad recall poisons decisions (tune distance + top-K).
- **Memory hygiene** — don't blindly trust old outcomes; weight by recency/reward, prune.
- **Write discipline** — decide *what's worth* remembering, not every token.

> One-liner: *"The agent learns the rubric from its own track record. I never hard-code the answer — that's what makes it real."*

---

## 8. How it actually runs (the plumbing)

- **Docker** runs Oracle 26ai Free as a container — a full database with one command, no
  install. `oracle/docker-compose.yml`.
- On first boot, files in `oracle/schema/` **auto-run** (the container executes them), so
  the tables, Duality View, memory table, and vector index exist immediately.
- **`oracledb` thin mode** — the Python driver talks to Oracle over the network in pure
  Python; no heavy Oracle client to install. `oracle/agent/db.py`.
- **FREEPDB1** is the pluggable database inside the container; **CCC** is our app schema.
- The MiniLM model is loaded once (`setup/01_load_onnx_model.sql`) before we generate embeddings.
- A **Python 3.12 venv** (not 3.14) avoids missing wheels for `oracledb`/`onnx`.

> One-liner: *"Docker gives me a real enterprise database in one command. The driver is pure Python. The schema sets itself up on first boot."*

---

## Glossary (for your students)

- **Embedding** — text represented as a vector of numbers capturing meaning.
- **Vector / cosine distance** — the numbers; how close two meanings are.
- **ANN / HNSW index** — fast approximate nearest-neighbour search over vectors.
- **Episodic / semantic / procedural memory** — what happened / facts / how-to.
- **JSON Relational Duality** — store relational, read/write as JSON, bidirectionally.
- **Tool use** — the model requests function calls; your code executes them.
- **Context window vs memory** — short-term working space vs durable, queryable store.
- **ONNX / in-DB model** — a portable model format Oracle runs itself, so embeddings need no external API.
