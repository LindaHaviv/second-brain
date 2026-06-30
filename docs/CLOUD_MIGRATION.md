# Cloud migration — local Oracle 26ai → Oracle Autonomous Database

Move the brain to a managed, always-on cloud database (encrypted, auto-backed-up, reachable from
a hosted MCP server). The app code is already cloud-ready — `db.py` connects to Autonomous over an
mTLS wallet when wallet env vars are set, with **no other code changes.** You do the provisioning
(your Oracle Cloud account); everything else is the steps below.

> Local stays fully working and private. You can run cloud and local side by side.

---

## Phase 1 — Provision the database (OCI console)
1. Sign in to **cloud.oracle.com**. (Always Free tier is enough to start.)
2. **Autonomous Database → Create Autonomous Database.**
   - Workload type: **Transaction Processing** (or "AI").
   - **Always Free**: on.
   - Database version: **23ai / 26ai**.
   - Set the **ADMIN password** (save it).
3. When it's **Available**, open it → **Database Connection → Download Wallet** (Instance Wallet).
   Set a **wallet password** (save it). **Unzip** it to a folder, e.g. `~/brain-wallet/`.
   - It contains `tnsnames.ora` (connection aliases like `mybrain_high`) and `ewallet.pem`.

## Phase 2 — Create a least-privilege app user
Open **Database Actions → SQL** (web worksheet) on the ADB, signed in as **ADMIN**, and run:
```sql
CREATE USER CCC IDENTIFIED BY "<StrongPwd>";
GRANT CREATE SESSION, RESOURCE, CREATE VIEW, CREATE MINING MODEL TO CCC;
ALTER USER CCC QUOTA UNLIMITED ON DATA;
-- for loading the ONNX model from object storage later:
GRANT EXECUTE ON DBMS_CLOUD TO CCC;
GRANT READ, WRITE ON DIRECTORY DATA_PUMP_DIR TO CCC;
```
> Least privilege on purpose — the app never runs as ADMIN.

## Phase 3 — Point the app at the cloud
In `oracle/.env` (gitignored), add:
```bash
DB_DSN=mybrain_high                 # an alias from the wallet's tnsnames.ora
DB_USER=CCC
APP_PWD=<StrongPwd>
DB_WALLET_DIR=/Users/you/brain-wallet
DB_WALLET_PASSWORD=<wallet password>
```
Test the connection:
```bash
cd oracle/agent && ../../.venv/bin/python -c "import db; print(db.connect().cursor().execute(
  \"select 'connected to '||sys_context('userenv','con_name') from dual\").fetchone()[0])"
```

## Phase 4 — Apply the schema
In **Database Actions → SQL** (as CCC, or as ADMIN with `ALTER SESSION SET CURRENT_SCHEMA=CCC`),
paste and run the schema files in order: `oracle/schema/01` → `07`.
(They create the content/duality tables, all four memory types, content chunks, and the wiki layer.)

## Phase 5 — Load the embedding model (in-DB, from object storage)
Autonomous can't read your laptop's `/models`, so stage the ONNX model in Object Storage:
1. **Object Storage → Buckets → Create Bucket** (e.g. `models`). Upload
   `oracle/models/all_MiniLM_L12_v2.onnx`.
2. On the object → **Create Pre-Authenticated Request** (read, object-only). Copy the PAR URL.
3. In **Database Actions → SQL** (as CCC):
```sql
BEGIN
  DBMS_CLOUD.GET_OBJECT(
    object_uri      => '<PAR URL>',
    directory_name  => 'DATA_PUMP_DIR');
  DBMS_VECTOR.LOAD_ONNX_MODEL(
    'DATA_PUMP_DIR', 'all_MiniLM_L12_v2.onnx', 'MINILM',
    JSON('{"function":"embedding","embeddingOutput":"embedding","input":{"input":["DATA"]}}'));
END;
/
-- verify:
SELECT VECTOR_EMBEDDING(MINILM USING 'hello' AS DATA) IS NOT NULL AS ok FROM dual;
```

## Phase 6 — Load your data (re-ingest from your local sources)
Cleanest path: with the cloud env set (Phase 3), re-run the loaders — embeddings regenerate in-DB
on the cloud, so nothing else changes:
```bash
./.venv/bin/python scripts/youtube.py
./.venv/bin/python scripts/youtube_transcripts.py
./.venv/bin/python scripts/notion.py          # your private loaders
./.venv/bin/python scripts/claude_chats.py
./.venv/bin/python scripts/claude_code.py
cd oracle/agent && ../../.venv/bin/python wiki.py     # recompile the wiki in the cloud
../../.venv/bin/python semantic_memory.py             # re-consolidate semantic memory
```
> Alternative: Oracle Data Pump / `DBMS_CLOUD` to move tables directly — heavier; re-ingest is
> simpler and reproducible (and it's what the tutorial shows).

## Phase 7 — Lock it down (security)
- **Wallet is a secret** — keep `DB_WALLET_DIR` outside the repo; never commit it (already gitignored).
- **Access control:** ADB → Network → restrict to an **access-control list** (your IP / the MCP
  host's egress) once the hosted MCP server is up.
- **Rotate** the ADMIN + CCC passwords and wallet on a schedule.
- The hosted **MCP server** (separate step — see [ARCHITECTURE.md](ARCHITECTURE.md)) is what then
  exposes this cloud brain to Claude/ChatGPT, with its own bearer auth.

---

### What's already done for you
`db.py` auto-detects the wallet env and connects over mTLS — so **all** the loaders, the agent,
the wiki, and the MCP server work against the cloud DB unchanged. Flip the env vars and you're on
cloud; remove them and you're back to local.
