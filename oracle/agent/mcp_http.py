"""Hosted MCP server — the same brain tools as mcp_server.py, but over HTTP so Claude (web/mobile),
ChatGPT, or any device can reach the brain over the internet.

It reuses the exact same FastMCP instance + tools from mcp_server.py and connects to whatever
oracle/.env points at (the cloud Autonomous DB). Auth: WorkOS OAuth + allowlist when AUTHKIT_DOMAIN
is set, else an `Authorization: Bearer $MCP_AUTH_TOKEN` header. Open probes:
  GET /health  — shallow liveness (no DB), for the load balancer's fast check
  GET /ready   — readiness: actually touches the DB (SELECT 1), 200 if reachable else 503

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
    """Periodically run an embedding so the Always-Free Autonomous DB doesn't idle out and the
    in-DB ONNX model stays resident — the first real query skips the cold path. Acquires and
    RELEASES a connection each cycle (with pooling on, holding one forever would hog a pool slot)."""
    interval = int(os.environ.get("KEEP_WARM_SECONDS", "240"))
    while True:
        conn = None
        try:
            conn = db.connect()
            with conn.cursor() as cur:
                cur.execute("SELECT VECTOR_EMBEDDING(MINILM USING 'warm' AS DATA) FROM dual").fetchone()
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        time.sleep(interval)


if os.environ.get("KEEP_WARM", "1") == "1":
    threading.Thread(target=_keep_warm, daemon=True).start()


def _readiness():
    """Deep health: prove the DB link actually works so the platform can route away from a wedged
    machine. Kept minimal — no error detail in the body (don't leak internals to an open probe)."""
    conn = None
    try:
        conn = db.connect()
        conn.cursor().execute("SELECT 1 FROM dual").fetchone()
        return JSONResponse({"ready": True})
    except Exception as e:
        print(f"[ready] DB check failed: {e}", flush=True)
        return JSONResponse({"ready": False}, status_code=503)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


class Gateway(BaseHTTPMiddleware):
    """Outermost middleware: serve the open probes and time/log tool traffic.
      /health -> shallow 200 (no DB)      /ready -> deep DB check
    Because this runs before auth, /health and /ready stay open in BOTH auth modes; everything
    else falls through to the auth layer. /mcp calls are timed and logged (path + status + latency,
    never query text / note bodies — no PII)."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/health":
            return JSONResponse({"ok": True})
        if path == "/ready":
            return _readiness()
        start = time.monotonic()
        resp = await call_next(request)
        if path.startswith("/mcp"):
            ms = int((time.monotonic() - start) * 1000)
            print(f"[mcp] {request.method} {path} -> {resp.status_code} {ms}ms", flush=True)
        return resp


class BearerAuth(BaseHTTPMiddleware):
    """Require `Authorization: Bearer <MCP_AUTH_TOKEN>`. Only mounted when WorkOS OAuth is off;
    open probes are already handled by Gateway (outer), so this only ever sees protected paths."""

    async def dispatch(self, request, call_next):
        if TOKEN and request.headers.get("authorization") != f"Bearer {TOKEN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


# When AUTHKIT_DOMAIN is set, FastMCP's WorkOS OAuth (+ allowlist) protects /mcp; otherwise the
# bearer-token middleware does. Gateway is outermost, so /health + /ready stay open in both.
# stateless_http=True: no in-memory session affinity, so tool calls work across multiple Fly
# machines (otherwise a call can land on a machine without the connect-time session).
_mw = [Middleware(Gateway)]
if not os.environ.get("AUTHKIT_DOMAIN"):
    _mw.append(Middleware(BearerAuth))
app = mcp.http_app(middleware=_mw, stateless_http=True)
