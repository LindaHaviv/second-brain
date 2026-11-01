"""ScopeMiddleware: the tool list and tool calls a caller sees are decided by their scopes.

Reads the caller from fastmcp's access token (set by the AuthKit verifier for OAuth, or
by the static verifier for named tokens), resolves scopes via scopes.py, and:
  on_list_tools  — drops tools the scopes don't allow (the client never learns they exist)
  on_call_tool   — refuses a call to a tool the scopes don't allow, even if guessed
No token (the local stdio server, MCP_ALLOW_ANON) -> owner, unchanged behaviour.
"""
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

import scopes as sc


def _principal(token):
    """The identity key for PRINCIPALS: a static token's client_id (we name them), else the
    OAuth subject (WorkOS sub)."""
    if token is None:
        return None
    cid = getattr(token, "client_id", None)
    sub = getattr(token, "subject", None) or (getattr(token, "claims", None) or {}).get("sub")
    # static tokens carry their name as client_id; OAuth tokens carry the MCP client's id,
    # which is not a person — prefer the sub whenever one exists
    return sub or cid


class ScopeMiddleware(Middleware):
    def __init__(self, principals=None, token_getter=None):
        self._principals = sc.load_principals() if principals is None else principals
        if token_getter is None:
            from fastmcp.server.dependencies import get_access_token
            token_getter = get_access_token
        self._get_token = token_getter

    def _scopes(self):
        try:
            tok = self._get_token()
        except Exception:          # outside a request (tests, stdio) -> owner
            tok = None
        if tok is None:
            return {sc.OWNER}
        # a static token can carry its scopes directly; PRINCIPALS wins when present
        who = _principal(tok)
        if who in self._principals:
            return sc.scopes_for(who, self._principals)
        direct = set(getattr(tok, "scopes", None) or [])
        if direct & sc.KNOWN_SCOPES:
            return direct & sc.KNOWN_SCOPES
        return sc.scopes_for(who, self._principals)   # allowlisted, unlisted -> owner

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        scopes = self._scopes()
        return [t for t in tools if sc.allowed(t.name, scopes)]

    async def on_call_tool(self, context, call_next):
        name = getattr(context.message, "name", None)
        if not sc.allowed(name, self._scopes()):
            raise ToolError(f"tool {name!r} is not available to this caller")
        return await call_next(context)
