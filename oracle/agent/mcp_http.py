"""Hosted MCP server — the same brain tools as mcp_server.py, but over HTTP with a bearer
token, so Claude (web/mobile), ChatGPT, or any device can reach the brain over the internet.

It reuses the exact same FastMCP instance + tools from mcp_server.py and connects to whatever
oracle/.env points at (the cloud Autonomous DB). Auth: every request must send
`Authorization: Bearer $MCP_AUTH_TOKEN` (set that env var); `/health` is open for uptime checks.

Run locally:
  MCP_AUTH_TOKEN=dev-secret ../../.venv/bin/uvicorn mcp_http:app --host 0.0.0.0 --port 8000
Deploy: see docs/HOSTED_MCP.md (Dockerfile + Fly.io).
"""
import os
import threading
import time

from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import db                       # noqa: E402
from mcp_server import mcp   # same FastMCP server + tools (search/fetch/wiki/topics/recent/ingest_note)

TOKEN = os.environ.get("MCP_AUTH_TOKEN")


def _keep_warm():
    """Hold one hot DB session and periodically run an embedding so the Always-Free Autonomous
    DB never idles out and the in-DB model stays resident — the first real query skips the cold
    path. Adapted from mhaviv/brain-mcp-server's keep-warm daemon."""
    interval = int(os.environ.get("KEEP_WARM_SECONDS", "240"))
    conn = None
    while True:
        try:
            if conn is None:
                conn = db.connect()
            with conn.cursor() as cur:
                cur.execute("SELECT VECTOR_EMBEDDING(MINILM USING 'warm' AS DATA) FROM dual").fetchone()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
        time.sleep(interval)


if os.environ.get("KEEP_WARM", "1") == "1":
    threading.Thread(target=_keep_warm, daemon=True).start()


class BearerAuth(BaseHTTPMiddleware):
    """Require `Authorization: Bearer <MCP_AUTH_TOKEN>` on every request (except /health)."""

    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return JSONResponse({"ok": True})
        if TOKEN and request.headers.get("authorization") != f"Bearer {TOKEN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


class _Health(BaseHTTPMiddleware):
    """Keep /health open (for uptime checks) regardless of the auth mode."""
    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return JSONResponse({"ok": True})
        return await call_next(request)


# When AUTHKIT_DOMAIN is set, FastMCP's WorkOS OAuth (+ email allowlist) protects /mcp; otherwise
# fall back to the bearer-token middleware. /health stays open in both.
# stateless_http=True: no in-memory session affinity, so it works across multiple Fly machines /
# load balancing (otherwise a tool call can land on a machine without the connect-time session).
_mw = [Middleware(_Health)]
if not os.environ.get("AUTHKIT_DOMAIN"):
    _mw.append(Middleware(BearerAuth))
app = mcp.http_app(middleware=_mw, stateless_http=True)
