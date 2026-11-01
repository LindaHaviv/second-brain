"""Per-caller SCOPES for the hosted brain: who may see which tools.

Until 2026-10-31 every authenticated caller was the owner: one allowlist, every tool.
That is right for one person and wrong the moment a second client (a personal-agent
app, a team org, a Slack agent) gets a token. Scopes are the smallest fix: each
principal (a WorkOS `sub` on the OAuth path, or a named static token) maps to a set of
scopes, and ScopeMiddleware registers ONLY the tools those scopes allow — an
out-of-scope tool is not hidden, it does not exist for that caller (tools/list omits it,
tools/call refuses it).

Scopes (pure strings; tools opt in by name below):
  content.read   search, fetch, wiki, topics, recent, related, by_series, overview,
                 source_status, list_agents  — what a teammate or a personal agent may read
  notes.write    ingest_note, save_chat       — add to the brain (never read more)
  backlog.write  backlog_add/edit/done/review (private tools; registered on the hosted server)
  diagram        diagram_from_video_url, upload_video_diagram
  owner          everything, including tools no scope lists (future tools default to owner)

Principals come from the PRINCIPALS env var, a JSON object (names only in docs; the value
lives in Fly secrets), e.g.
  {"user_01ABC...": ["owner"],                       # Linda's WorkOS sub
   "user_01DEF...": ["content.read"],                # a teammate's WorkOS sub
   "pearl":        ["content.read", "notes.write"]}  # a named static token (see mcp_http)
A sub on ALLOWED_SUBS/ALLOWED_EMAILS with no PRINCIPALS entry keeps OWNER — so an empty
PRINCIPALS changes nothing (zero behaviour change on deploy); the first entry you add is
the first caller who is NOT you.
"""
import json
import os

OWNER = "owner"

TOOL_SCOPES = {
    # content.read
    "search": "content.read", "fetch": "content.read", "wiki": "content.read",
    "topics": "content.read", "recent": "content.read", "related": "content.read",
    "by_series": "content.read", "overview": "content.read",
    "source_status": "content.read", "list_agents": "content.read",
    # notes.write
    "ingest_note": "notes.write", "save_chat": "notes.write",
    # backlog.write (private layer)
    "backlog_add": "backlog.write", "backlog_edit": "backlog.write",
    "backlog_done": "backlog.write", "backlog_review": "backlog.write",
    # diagram (private layer)
    "diagram_from_video_url": "diagram", "upload_video_diagram": "diagram",
}
KNOWN_SCOPES = {OWNER, *TOOL_SCOPES.values()}


def load_principals(raw=None):
    """PRINCIPALS JSON -> {principal: set(scopes)}; unknown scope names are refused loudly
    (a typo must not silently grant or deny)."""
    raw = os.environ.get("PRINCIPALS", "") if raw is None else raw
    if not raw.strip():
        return {}
    data = json.loads(raw)
    out = {}
    for who, scopes in data.items():
        scopes = set(scopes if isinstance(scopes, list) else [scopes])
        bad = scopes - KNOWN_SCOPES
        if bad:
            raise ValueError(f"PRINCIPALS: unknown scope(s) {sorted(bad)} for {who!r}; "
                             f"known: {sorted(KNOWN_SCOPES)}")
        out[who] = scopes
    return out


def scopes_for(principal, principals, default_owner=True):
    """Scope set for a principal. Not listed -> owner (the allowlist already admitted them)
    when default_owner, else nothing."""
    if principal in principals:
        return set(principals[principal])
    return {OWNER} if default_owner else set()


def allowed(tool_name, scopes):
    """May a caller with these scopes use this tool? Owner: always. Otherwise the tool's
    scope must be held; a tool no scope lists is owner-only by construction."""
    if OWNER in scopes:
        return True
    need = TOOL_SCOPES.get(tool_name)
    return bool(need) and need in scopes


def filter_tools(names, scopes):
    return [n for n in names if allowed(n, scopes)]
