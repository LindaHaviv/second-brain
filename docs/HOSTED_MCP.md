# Hosted MCP — reach your brain from Claude, ChatGPT, and your phone

The local MCP server (`mcp_server.py`, stdio) only works on this machine. To use the brain from
**Claude web/mobile, ChatGPT, or any device**, the MCP server must be **online** with auth. This
deploys `mcp_http.py` (the same tools, over HTTP + a bearer token) to Fly.io, pointing at the
cloud Autonomous DB.

> Already verified locally: `/health` → 200, no token → 401, valid token → reaches the tools.

## Connection model: walletless TLS (simplest for a server)
A container is cleanest without shipping wallet files, and `python-oracledb` supports plain **TLS**
to Autonomous (no wallet) once TLS auth is enabled. So the container just needs a connection
string + credentials as secrets.

### 1. Enable TLS + restrict access on the DB (also part of hardening)
In the console → **second-brain → Network**:
- Set **Mutual TLS (mTLS) authentication: Not required** (allows TLS).
- Under **Access control list**, add the Fly app's egress IPs (or leave open *only* briefly for
  first deploy, then lock down). With TLS + a strong ADMIN/app password + the ACL, this is secure.

### 2. Get the TLS connection string
Console → **second-brain → Database connection → Connection strings → TLS** → copy the
`secondbrain_high` descriptor (a long `(description=...(protocol=tcps)...)` string).

## Deploy to Fly.io
```bash
# one-time
brew install flyctl
fly auth login                      # your Fly account (free allowance covers this)

# from the repo root
fly launch --no-deploy --copy-config --name my-second-brain   # uses deploy/fly.toml

# secrets (never baked into the image)
fly secrets set \
  DB_DSN='<the TLS connection string>' \
  DB_USER='CCC' \
  APP_PWD='<the CCC app-user password>' \
  MCP_AUTH_TOKEN='<a long random token you generate>'

fly deploy
```
Your server is then at `https://<your-app>.fly.dev` (health: `/health`, MCP: `/mcp`).

> Prefer mTLS over walletless? Ship the wallet as a Fly secret (base64 the zip, decode at start to
> a dir, set `DB_WALLET_DIR`/`DB_WALLET_PASSWORD`). `db.py` already supports the wallet path.

## Connect clients
- **Claude Code / Claude Desktop / the API:** point them at `https://<your-app>.fly.dev/mcp`
  with header `Authorization: Bearer <MCP_AUTH_TOKEN>`. `search`/`fetch` follow the standard
  connector contract (`{results:[{id,title,url,text}]}`), plus `wiki`, `topics`, `recent`,
  `ingest_note`.
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
3. In claude.ai / ChatGPT → add a custom connector with URL `https://<your-app>.fly.dev/mcp` (no
   token) → log in via WorkOS. **Only the allow-listed user gets in** — everyone else is denied.

> **Security:** OAuth *authenticates*; the `ALLOWED_EMAILS` allowlist *authorizes* — only your
> email(s) get in, even though anyone can attempt a WorkOS login. The server **refuses to start**
> with an empty allowlist. Full guidance: [SECURITY.md](../SECURITY.md).

## Keep-warm (built in)
The server runs a background keep-warm thread (`KEEP_WARM=1`, every `KEEP_WARM_SECONDS`, default
240s) holding a hot DB session and keeping the in-DB model resident — so the Always-Free
Autonomous DB doesn't idle out and the first real query skips the cold path.

## Security
- **HTTPS enforced** (`force_https`), **bearer token required** on every request (`/health` open).
- **Rotate** `MCP_AUTH_TOKEN` periodically (`fly secrets set ...` redeploys).
- Keep the DB **ACL** tight (Fly egress IPs). Consider a least-privilege DB user (see hardening).
- Token is the only credential a client needs — treat it like a password.
- **Read/write separation.** Read tools are annotated `readOnlyHint` (clients can auto-allow them);
  the one write tool (`ingest_note`) is annotated as a write so clients gate it. To make the hosted
  server **read-only** — recommended unless you actually ingest *through* the connector — set
  `fly secrets set MCP_READONLY=1` and the write tool isn't registered at all.
