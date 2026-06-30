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
Your server is then at `https://my-second-brain.fly.dev` (health: `/health`, MCP: `/mcp`).

> Prefer mTLS over walletless? Ship the wallet as a Fly secret (base64 the zip, decode at start to
> a dir, set `DB_WALLET_DIR`/`DB_WALLET_PASSWORD`). `db.py` already supports the wallet path.

## Connect clients
- **Claude (web/desktop/mobile) & ChatGPT:** add a custom MCP/connector with URL
  `https://my-second-brain.fly.dev/mcp` and header `Authorization: Bearer <MCP_AUTH_TOKEN>`.
- Tools available: `search`, `fetch`, `wiki`, `topics`, `recent`, `ingest_note`.

## Security
- **HTTPS enforced** (`force_https`), **bearer token required** on every request (`/health` open).
- **Rotate** `MCP_AUTH_TOKEN` periodically (`fly secrets set ...` redeploys).
- Keep the DB **ACL** tight (Fly egress IPs). Consider a least-privilege DB user (see hardening).
- Token is the only credential a client needs — treat it like a password.
