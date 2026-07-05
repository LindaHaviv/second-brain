"""Hosted MCP server — the same brain tools as mcp_server.py, but over HTTP so Claude (web/mobile),
ChatGPT, or any device can reach the brain over the internet.

It reuses the exact same FastMCP instance + tools from mcp_server.py and connects to whatever
oracle/.env points at (the cloud Autonomous DB). Auth: WorkOS OAuth + allowlist when AUTHKIT_DOMAIN
is set, else an `Authorization: Bearer $MCP_AUTH_TOKEN` header. Open probes:
  GET /health  — shallow liveness (no DB), for the load balancer's fast check
  GET /ready   — readiness: actually touches the DB (SELECT 1), 200 if reachable else 503

Run locally:
  MCP_AUTH_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))') \\
      ../../.venv/bin/uvicorn mcp_http:app --host 0.0.0.0 --port 8000
Deploy: see docs/HOSTED_MCP.md (Dockerfile + Fly.io).
"""
import hmac
import os
import threading
import time

from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import db                       # noqa: E402
from mcp_server import mcp   # same FastMCP server + all tools (search/fetch/wiki/topics/recent/by_series/overview/ingest_note)

TOKEN = os.environ.get("MCP_AUTH_TOKEN")

# FAIL CLOSED: a hosted brain must never start without auth. OAuth (AUTHKIT_DOMAIN) or a bearer
# token (MCP_AUTH_TOKEN) — one of them is required. MCP_ALLOW_ANON=1 is an explicit, deliberate
# escape hatch for local experiments only (never set it on a public deployment).
if (not os.environ.get("AUTHKIT_DOMAIN") and not TOKEN
        and os.environ.get("MCP_ALLOW_ANON") != "1"):
    raise SystemExit(
        "refusing to start with NO auth configured — set AUTHKIT_DOMAIN (OAuth) or "
        "MCP_AUTH_TOKEN (bearer), or MCP_ALLOW_ANON=1 for a local-only experiment.")

# a bearer token on a public URL must not be guessable: enforce a floor when it is
# the only auth (OAuth deployments may still set a strong token for API clients).
if TOKEN and not os.environ.get("AUTHKIT_DOMAIN") and len(TOKEN) < 32:
    raise SystemExit(
        "MCP_AUTH_TOKEN is too short for a public deployment (min 32 chars). "
        "Generate one:  python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"")


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


_READY_CACHE = {"at": 0.0, "ok": False}   # /ready is unauthenticated — cache so it can't be
_READY_TTL = 10.0                          # hammered to hold DB pool slots (cheap DoS guard)


def _readiness():
    """Deep health: prove the DB link actually works so the platform can route away from a wedged
    machine. Result cached ~10s (the probe is open to the internet; uncached, a request loop could
    monopolize the small connection pool). No error detail in the body — don't leak internals."""
    now = time.monotonic()
    if now - _READY_CACHE["at"] < _READY_TTL:
        return JSONResponse({"ready": _READY_CACHE["ok"]},
                            status_code=200 if _READY_CACHE["ok"] else 503)
    conn = None
    try:
        conn = db.connect()
        conn.cursor().execute("SELECT 1 FROM dual").fetchone()
        _READY_CACHE.update(at=now, ok=True)
        return JSONResponse({"ready": True})
    except Exception as e:
        print(f"[ready] DB check failed: {e}", flush=True)
        _READY_CACHE.update(at=now, ok=False)
        return JSONResponse({"ready": False}, status_code=503)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


class _Bucket:
    """Tiny token-bucket rate limiter (MCP spec: servers MUST rate-limit tool invocations).
    Single-tenant server, so one global bucket is enough — burst of RATE_BURST, refills
    RATE_PER_SEC/s. Not distributed (per-machine), which is fine at this scale."""

    def __init__(self, burst=30, per_sec=5.0):
        self.capacity = float(os.environ.get("RATE_BURST", burst))
        self.rate = float(os.environ.get("RATE_PER_SEC", per_sec))
        self.tokens, self.at, self.lock = self.capacity, time.monotonic(), threading.Lock()

    def allow(self):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.at) * self.rate)
            self.at = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


_bucket = _Bucket()


class Gateway(BaseHTTPMiddleware):
    """Outermost middleware: serve the open probes, rate-limit, and time/log tool traffic.
      /health -> shallow 200 (no DB)      /ready -> deep DB check (cached ~10s)
    Because this runs before auth, /health and /ready stay open in BOTH auth modes; everything
    else falls through to the auth layer. /mcp calls are timed and logged (path + status + latency,
    never query text / note bodies — no PII)."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/health":
            return JSONResponse({"ok": True})
        if path == "/ready":
            return _readiness()
        if path == "/diagram" or path.startswith("/diagram/file/"):
            # video->diagram intake: its own token auth (DIAGRAM_TOKEN); 404 when disabled.
            # CORS open: the MCP Apps widget posts from a sandboxed (cross-origin) iframe;
            # auth travels in the form body, never cookies, so a wildcard origin is safe.
            import diagram_ext
            if not diagram_ext.enabled():
                return JSONResponse({"error": "not found"}, status_code=404)
            if request.method == "OPTIONS":
                resp = JSONResponse({"ok": True})
            elif path.startswith("/diagram/file/"):
                resp = diagram_ext.serve_file(path.rsplit("/", 1)[1])
            else:
                if request.method == "POST" and not _bucket.allow():
                    return JSONResponse({"error": "rate limited — slow down"},
                                        status_code=429)
                resp = await diagram_ext.handle(request)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "*"
            return resp
        if path.startswith("/mcp") and not _bucket.allow():
            return JSONResponse({"error": "rate limited — slow down"}, status_code=429)
        start = time.monotonic()
        resp = await call_next(request)
        if path.startswith("/mcp"):
            ms = int((time.monotonic() - start) * 1000)
            print(f"[mcp] {request.method} {path} -> {resp.status_code} {ms}ms", flush=True)
        return resp


class BearerAuth(BaseHTTPMiddleware):
    """Require `Authorization: Bearer <MCP_AUTH_TOKEN>`. Only mounted when WorkOS OAuth is off;
    open probes are already handled by Gateway (outer), so this only ever sees protected paths.
    compare_digest = constant-time comparison (no timing oracle on the token)."""

    async def dispatch(self, request, call_next):
        if TOKEN:
            supplied = request.headers.get("authorization") or ""
            if not hmac.compare_digest(supplied, f"Bearer {TOKEN}"):
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
