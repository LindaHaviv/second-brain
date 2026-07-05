# Hosted MCP — reach your brain from Claude, ChatGPT, and your phone

The local MCP server (`mcp_server.py`, stdio) only works on this machine. To use the brain from
**Claude web/mobile, ChatGPT, or any device**, the MCP server must be **online** with auth. This
deploys `mcp_http.py` (the same tools, over HTTP + a bearer token) to Fly.io, pointing at the
cloud Autonomous DB.

> **Build vs. managed.** This doc covers the **custom** self-hosted route — full control, Python
> tools, the claude.ai/ChatGPT web-connector OAuth flow (reachable from your phone), portable across
> databases, and it works with the local container too. That's the right fit for this build.
> Oracle also ships a fully **managed** [Autonomous AI Database MCP Server](https://www.oracle.com/autonomous-database/mcp-server/)
> built into Autonomous AI Database (Select AI Agent PL/SQL tools, governed by DB identity, native
> auditing) — the zero-ops path when your brain lives in Autonomous AI Database and PL/SQL tools
> cover your needs.

> Already verified locally: `/health` → 200, no token → 401, valid token → reaches the tools.

## Connection model: walletless TLS (simplest for a server)
A container is cleanest without shipping wallet files, and `python-oracledb` supports plain **TLS**
to Autonomous (no wallet) once TLS auth is enabled. So the container just needs a connection
string + credentials as secrets.

### 1. Enable TLS + restrict access on the DB (also part of hardening)
In the console → **<your-db> → Network**:
- Set **Mutual TLS (mTLS) authentication: Not required** (allows TLS).
- Under **Access control list**, add the Fly app's egress IPs (or leave open *only* briefly for
  first deploy, then lock down). With TLS + a strong ADMIN/app password + the ACL, this is secure.

### 2. Get the TLS connection string
Console → **<your-db> → Database connection → Connection strings → TLS** → copy the
`<your-db>_high` descriptor (a long `(description=...(protocol=tcps)...)` string).

## Deploy to Fly.io
```bash
# one-time
brew install flyctl
fly auth login                      # your Fly account (free allowance covers this)

# from the repo root
fly launch --no-deploy --copy-config --name <your-app>   # uses the root fly.toml

# secrets (never baked into the image)
fly secrets set \
  DB_DSN='<the TLS connection string>' \
  DB_USER='CCC' \
  APP_PWD='<the CCC app-user password>' \
  MCP_AUTH_TOKEN='<a long random token you generate>'

fly deploy
```
Your server is then at `https://<your-app>.fly.dev` (health: `/health`, MCP: `/mcp`).

> Prefer mTLS over walletless? Ship the wallet as a Fly secret: tar.gz the **unzipped** wallet dir,
> base64 it into the `BRAIN_WALLET_B64` secret (the entrypoint decodes it at start), and set
> `DB_WALLET_DIR`/`DB_WALLET_PASSWORD`. `db.py` already supports the wallet path.

## Connect clients
- **Claude Code / Claude Desktop / the API:** point them at `https://<your-app>.fly.dev/mcp`
  with header `Authorization: Bearer <MCP_AUTH_TOKEN>`. `search`/`fetch` follow the standard
  connector contract (`{results:[{id,title,url,text}]}`), plus `wiki`, `topics`, `recent`,
  `by_series`, `overview`, and `ingest_note` (the one write tool).
- **ChatGPT + claude.ai web/mobile:** these connector UIs require **OAuth** (Dynamic Client
  Registration), not a bearer header. This repo supports it via **WorkOS AuthKit** — see below.

## OAuth for claude.ai / ChatGPT (WorkOS AuthKit + email allowlist)
The code is built in (`mcp_server.py` → `_build_auth`); turn it on with a WorkOS account + env:
1. **WorkOS** (free): create a workspace. In **Connect → Configuration**, enable **Dynamic Client
   Registration** (and CIMD), and under **MCP resource indicators** add
   `https://<your-app>.fly.dev/mcp`. Get your **AuthKit domain** from the **Domains** page (looks
   like `https://<slug>.authkit.app`).
2. Set secrets and redeploy:
   ```bash
   fly secrets set \
     AUTHKIT_DOMAIN='https://<slug>.authkit.app' \
     MCP_BASE_URL='https://<your-app>.fly.dev' \
     ALLOWED_SUBS='user_...'     # your WorkOS user id — ONLY this user is authorized
   fly deploy
   ```
   > WorkOS **AuthKit access tokens carry `sub` (the user id), not email** — so authorize by
   > **`ALLOWED_SUBS`**. Find your id at **Users → your user → the `user_...` value** (after your
   > first login). (`ALLOWED_EMAILS` also works for providers that put email in the token.)
3. Add the connector in each client (same URL, no token — the OAuth login handles it):
   - **claude.ai (web + mobile):** Settings → **Connectors** → **Add custom connector** → paste
     `https://<your-app>.fly.dev/mcp` → complete the WorkOS login. Once added on the web it's
     available in the mobile app too.
   - **ChatGPT:** Settings → **Apps & Connectors** → enable **Developer mode** (required for
     custom MCP connectors) → **Create** → paste the same `/mcp` URL, auth = **OAuth** → complete
     the login. ChatGPT uses the `search`/`fetch` connector contract this server implements.
   - **Claude Desktop:** the same custom-connector UI works for the hosted URL — or skip hosting
     entirely and run it local over stdio (see [TUTORIAL Lab 7](TUTORIAL.md)).

   **Only the allow-listed user gets in** — everyone else is denied after login.

> **Security:** OAuth *authenticates*; the allowlist (`ALLOWED_SUBS` and/or `ALLOWED_EMAILS`)
> *authorizes* — only your account gets in, even though anyone can attempt a WorkOS login. The
> server **refuses to start** with an empty allowlist — and refuses to start with **no auth
> configured at all** (no silent fail-open). Full guidance: [SECURITY.md](../SECURITY.md).

## Keep-warm + connection pool (built in)
- **Session pool** — the server reuses DB connections from a per-process pool (`db.py`), so each
  tool call skips a fresh mTLS connect to the cloud DB (hundreds of ms) and concurrent sessions are
  capped (`DB_POOL_MAX`, default 4). Set `DB_POOL=0` to fall back to direct connects.
- **Keep-warm** — a background thread (`KEEP_WARM=1`, every `KEEP_WARM_SECONDS`, default 240s) runs
  an embedding so the Always-Free Autonomous DB doesn't idle out and the in-DB ONNX model stays
  resident, so the first real query skips the cold path.

## Health & readiness
- `GET /health` — shallow liveness (no DB); the load balancer's fast check. Open in both auth modes.
- `GET /ready` — **touches the DB** (`SELECT 1`) → `200 {"ready":true}` or `503`. Point external
  uptime monitoring at this so you're alerted when a machine's DB link is wedged (not just when the
  process is up). The result is cached ~10s (the probe is open — caching stops a request loop from
  holding DB pool slots). Each `/mcp` call is also logged with method + status + latency (no query text).

## Security
- **HTTPS enforced** (`force_https`); **auth required on every request** (`/health`+`/ready` open) —
  the server refuses to start with neither OAuth nor a bearer token configured, and `/mcp` is
  rate-limited (token bucket).
- **Rotate** `MCP_AUTH_TOKEN` periodically (`fly secrets set ...` redeploys).
- Keep the DB **ACL** tight (Fly egress IPs). Consider a least-privilege DB user (see hardening).
- Token is the only credential a client needs — treat it like a password.
- **Read/write separation.** Read tools are annotated `readOnlyHint` (clients can auto-allow them);
  the one write tool (`ingest_note`) is annotated as a write so clients gate it. To make the hosted
  server **read-only** — recommended unless you actually ingest *through* the connector — set
  `fly secrets set MCP_READONLY=1` and the write tool isn't registered at all.

## Extending the hosted server (private tools, public engine)

The server ships two import hooks so you can add personal tools and routes WITHOUT forking
the public code: `mcp_server` tries `import server_ext` and calls
`server_ext.register(mcp, ...)` (register extra tools/resources — including MCP Apps UI
panels — and **MCP prompts**: expose your brain-stored workflow notes as native prompts,
fetched live per call, so every client lists your procedures like slash commands and an
edit to the note updates every client at once), and `mcp_http` tries `import http_ext` and gives `http_ext.maybe_handle(request,
rate_limit_ok)` first look at every request (return `None` to pass through).

Deploy by layering your private module(s) into the image next to the agent code — a private
Dockerfile that does `COPY yourprivate/*.py ./agent/` after copying `oracle/agent/`, plus its
own `Dockerfile.dockerignore` admitting that directory. Your workflows stay in your private
repo; the public repo stays a teaching artifact. (This split is how the reference deployment
runs its own video→diagram intake.)
