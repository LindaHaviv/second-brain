"""A minimal chat agent over this brain's own MCP tools — the engine of a
phone-chat front door.

Given a user message, run a small tool-use loop against the Anthropic API,
where the tools are the SAME tools this server already exposes over MCP
(called in-process via fastmcp's in-memory client — no network hop, no second
registry to drift). The persona, the tool allowlist, and the reply transport
are the caller's business; this module owns only the loop.

Design constraints, deliberately:
  - stdlib HTTP only (urllib) — no new runtime dependency for the image.
  - hard caps everywhere: tool turns, output tokens, tool-result size. A chat
    front door must be boring and bounded, not clever and unbounded.
  - tools are filtered by an explicit allowlist from the caller. New server
    tools never leak into the chat surface by default.

`to_anthropic_tools` is pure (schema translation) and pinned by
tests/test_chat_agent.py; the loop itself is thin I/O around it.
"""
import json
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_TOOL_TURNS = 6
MAX_TOKENS = 1024
TOOL_RESULT_CAP = 6000   # chars per tool result handed back to the model


def to_anthropic_tools(tools, allow):
    """MCP tool listings -> Anthropic tool definitions, filtered by the allowlist.
    `tools`: iterable with .name/.description/.inputSchema (or dicts).
    Returns a list in allowlist order dropped of anything the server lacks."""
    def field(t, name, default=""):
        return getattr(t, name, None) or (t.get(name, default) if isinstance(t, dict) else default)

    by_name = {}
    for t in tools:
        by_name[field(t, "name")] = {
            "name": field(t, "name"),
            "description": (field(t, "description") or "")[:1000],
            "input_schema": field(t, "inputSchema", {}) or {"type": "object"},
        }
    return [by_name[n] for n in allow if n in by_name]


def _call_api(api_key, model, system, messages, tools):
    payload = {"model": model, "max_tokens": MAX_TOKENS, "system": system,
               "messages": messages, "tools": tools}
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-api-key": api_key,
                 "anthropic-version": API_VERSION})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


async def respond(mcp, api_key, model, persona, user_text, tool_allow):
    """One user message -> one final text, via the in-memory MCP client.
    Every tool call is served by the same registry the MCP clients use."""
    import anyio
    from fastmcp import Client

    async with Client(mcp) as client:
        tools = to_anthropic_tools(await client.list_tools(), tool_allow)
        messages = [{"role": "user", "content": user_text}]
        for _ in range(MAX_TOOL_TURNS):
            resp = await anyio.to_thread.run_sync(
                lambda: _call_api(api_key, model, persona, messages, tools))
            blocks = resp.get("content", [])
            messages.append({"role": "assistant", "content": blocks})
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not uses or resp.get("stop_reason") != "tool_use":
                break
            results = []
            for u in uses:
                try:
                    out = await client.call_tool(u["name"], u.get("input") or {})
                    text = "\n".join(
                        c.text for c in out.content
                        if getattr(c, "type", "") == "text")[:TOOL_RESULT_CAP]
                except Exception as e:
                    text = f"(tool error: {type(e).__name__}: {e})"[:500]
                results.append({"type": "tool_result", "tool_use_id": u["id"],
                                "content": text or "(empty)"})
            messages.append({"role": "user", "content": results})
        final = [b.get("text", "") for b in messages[-1]["content"]
                 if isinstance(b, dict) and b.get("type") == "text"] \
            if messages[-1]["role"] == "assistant" else []
        if not final:   # loop ended on a tool turn — ask for the wrap-up
            resp = await anyio.to_thread.run_sync(
                lambda: _call_api(api_key, model, persona, messages + [
                    {"role": "user",
                     "content": "Wrap up: answer from what you have, briefly."}], []))
            final = [b.get("text", "") for b in resp.get("content", [])
                     if b.get("type") == "text"]
        return "\n".join(t for t in final if t).strip() or "(no reply)"
