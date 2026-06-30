# Security

This brain can hold a lot of *your* data. Here's how to keep it private and safe — read this
before you point it at your real content or put it online.

## Secrets — never commit them
- `.gitignore` already excludes `oracle/.env`, the Autonomous **wallet** (`*-wallet/`,
  `ewallet.*`, `cwallet.sso`, `tnsnames.ora`, …), `private/`, models, and `sources/`. Keep it that
  way — **don't force-add** any of those.
- Keep the canonical copy of every secret (DB passwords, wallet password, API keys, the MCP token)
  in a **password manager**, not just in `.env`.
- **Rotate** anything that's ever been exposed (pasted in chat, screenshared, committed by
  accident): the Anthropic key, Notion token, DB passwords, the MCP token, and the wallet.
- The passwords shipped in `.env.example` (`CHANGE_ME_SysPwd1`, `CHANGE_ME_AppPwd1`) are **local demo
  defaults only** — change them for anything real.

## Redact before you ingest
- AI-chat and coding-session transcripts frequently contain **API keys / tokens**. The loaders
  scrub known secret patterns before inserting; keep that on for every source.
- Run **`python scripts/review.py`** periodically — it scans the ingested content for leaked-secret
  patterns and exits non-zero if it finds any (wire it to an alert if you schedule it).
- **Minimize**: ingest summaries/curated data, not raw dumps. Keep financials, contracts, and
  contacts **out** of the brain. The most sensitive data can stay local and never go to the cloud.

## Database
- Use a **least-privilege app user** (the cloud setup creates `CCC`) — don't run the app or the MCP
  server as `ADMIN`.
- **Never expose the database to the public internet.** The MCP server is the only thing that talks
  to it; clients talk only to the MCP server.
- Cloud (Autonomous): connect over the **mTLS wallet** (treat it like a password) or walletless TLS
  with a **network ACL**. Encryption at rest is on by default.

## Hosted MCP server (if you put it online)
- **Require auth on every request.** A bearer token works for Claude Code/Desktop/API; for
  **claude.ai web/mobile and ChatGPT** you need **OAuth** (this repo uses WorkOS AuthKit + DCR).
- **Allowlist who can get in.** OAuth alone lets *anyone* with a valid login authenticate — gate it
  with an **email allowlist** (`ALLOWED_EMAILS`) so only you get access. The server is
  **fail-closed**: it refuses to start if the allowlist is empty.
- **HTTPS only** (enforced), secrets in your host's secret store (Fly secrets / a vault) — never in
  the image. **Rotate** the MCP token / re-deploy if a secret leaks.

## Prompt-injection (LLM-specific)
- Treat everything the brain returns (chats, web pages, emails) as **untrusted data, not
  instructions** — never let retrieved content override the agent's system prompt.
- **Gate write tools.** `ingest_note` is additive/low-risk; anything destructive or that touches an
  external system (e.g. updating Notion) should be **human-in-the-loop**.

## Quick checklist before going public or online
- [ ] `git status` clean of `.env`, wallet, `private/`, real content
- [ ] demo passwords changed; real secrets only in `.env` (gitignored) + a password manager
- [ ] `scripts/review.py` reports no leaked secrets
- [ ] app/MCP run as a least-privilege user, DB not publicly reachable
- [ ] hosted MCP: OAuth + email allowlist on, HTTPS, secrets in a vault
