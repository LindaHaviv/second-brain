# Oracle 26ai — Agent Memory Demo (the buildable, filmable core)

This is the technical heart of the video: **a content command center whose agent has persistent,
queryable memory.** Memory lives in Oracle 26ai as *relational* (auditable outcomes) + *vector*
(semantic recall) in one engine.

```
oracle/
├── docker-compose.yml          Oracle 26ai Free, local, arm64-friendly
├── schema/                     auto-runs on first DB boot (pure DDL, no external files)
│   ├── 01_content_duality.sql  posts/brands/deals + a JSON Relational Duality View
│   └── 02_agent_memory.sql  ★  episodic memory table + vector index + audit view
├── setup/                      run manually, once, after the DB is up
│   ├── 01_load_onnx_model.sql  register the in-DB embedding model (MINILM, 384-dim)
│   └── 02_retrieval_examples.sql  the teachable recall queries
└── agent/                   ★  the Claude agent loop with persistent memory
    ├── db.py                   Oracle connection (oracledb thin mode)
    ├── memory.py               record() + recall() + tool_stats() over agent_memory
    ├── tools.py                publish_hook scorer (latent rubric the agent must learn)
    ├── agent.py                manual loop: recall → draft → publish → record
    └── demo_same_task_twice.py the money shot: run 2 beats run 1 from memory
```

The agent uses **Claude (`claude-opus-4-8`, adaptive thinking)** for reasoning and
**Oracle's in-DB MiniLM** for embeddings — no external embedding API. It's a *manual*
agentic loop on purpose: we read memory before the model acts and write memory after each
tool call, which is the part the video is about.

## Prerequisites (one-time)

1. **Docker Desktop** — not yet installed on this machine. Install:
   ```
   brew install --cask docker      # then launch Docker Desktop once to start the engine
   ```
2. **Python 3.12 venv** — Python 3.14 is here but `oracledb`/LangChain/ONNX wheels may lag it.
   ```
   brew install python@3.12
   python3.12 -m venv .venv && source .venv/bin/activate
   pip install oracledb python-dotenv          # + agent libs once the loop lands
   ```

## Run it

```bash
cd oracle
cp .env.example .env            # set ORACLE_PWD / APP_PWD (or accept defaults)
docker compose up -d
docker compose logs -f oracle   # wait for: "DATABASE IS READY TO USE!"
```
On first boot the `schema/` scripts create the content tables, the Duality View, the
`agent_memory` table, and the vector index — all in the `CCC` schema.

Connect (SQLcl, DBeaver, or the `oracledb` driver):
`localhost:1521` · service `FREEPDB1` · user `CCC` · password `${APP_PWD}`

## Load the embedding model (once)

Embeddings are generated **inside the database** — no external embedding API.

1. Download Oracle's prebuilt **all-MiniLM-L12-v2** ONNX model (Oracle publishes it for in-DB use).
2. Put the `.onnx` file in a folder the container can read (e.g. mount `./models` into the
   container) and create the directory object, then run the loader:
   ```sql
   CREATE OR REPLACE DIRECTORY VEC_MODELS AS '/models';
   @setup/01_load_onnx_model.sql
   ```
3. Smoke test:
   ```sql
   SELECT VECTOR_EMBEDDING(MINILM USING 'hello world' AS DATA) FROM dual;
   ```

## The demo flow (what you film)

```bash
cd oracle/agent
python3.12 -m venv ../../.venv && source ../../.venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # DB vars come from oracle/.env
python demo_same_task_twice.py
```

1. **Run a task once** → the agent drafts a hook, tests it, and *writes a memory row* (action +
   outcome + a vector). Show the row + embedding landing in `agent_memory`.
2. **Run the same task again** → the agent first *recalls relevant memory* (`memory.recall`,
   the query in `setup/02_retrieval_examples.sql`), sees what scored well last time, and writes a
   better hook.
3. **Show it compounding** → run #2 scores higher because it remembered — not because we told it
   the answer (the scoring rubric in `tools.py` is latent; the agent learns it from outcomes).
4. **The auditable flex** → `SELECT * FROM tool_stats;` — the agent's success rate per tool, in
   plain SQL over the same memory a vector-only store couldn't give you.

## Why a database (the dev takeaway)

- Memory must be **durable** (survives restarts), **queryable** (SQL + semantic), **structured**
  (success/failure tracking), and **auditable**. A pile of JSON files gives you none of that.
- One engine doing relational + vector = no separate vector DB to sync. That's the consolidation
  story, and it's also what makes the "self-improving" loop honest instead of hand-wavy.

## The honest hard parts (say these on camera)

- Retrieval relevance — recall noise poisons decisions; tune the distance + top-K.
- Memory hygiene — don't blindly trust past outcomes; weight by recency/reward, prune stale rows.
- Write discipline — decide *what* is worth remembering, not every token.
