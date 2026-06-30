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

from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from mcp_server import mcp   # same FastMCP server + tools (search/fetch/wiki/topics/recent/ingest_note)

TOKEN = os.environ.get("MCP_AUTH_TOKEN")


class BearerAuth(BaseHTTPMiddleware):
    """Require `Authorization: Bearer <MCP_AUTH_TOKEN>` on every request (except /health)."""

    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return JSONResponse({"ok": True})
        if TOKEN and request.headers.get("authorization") != f"Bearer {TOKEN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


# Starlette/ASGI app exposing the MCP endpoint at /mcp, gated by the bearer middleware.
app = mcp.http_app(middleware=[Middleware(BearerAuth)])
