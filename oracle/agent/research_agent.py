"""The research agent — Claude, grounded in YOUR content, with memory.

This ties the whole thesis together:
  • KNOWLEDGE  = your posts (semantic search via content.py)
  • MEMORY     = past research runs (agent_memory via memory.py)
  • both live in ONE Oracle database, retrieved together.

The agent searches your content, synthesizes a grounded answer, cites your videos,
and records what it found so future questions build on past research.
"""
import json
import anthropic

from memory import record, recall
from content import search_content, get_post
from semantic_memory import semantic_recall

MODEL = "claude-opus-4-8"

SYSTEM = (
    "You are a research assistant for Linda. Research the question using BOTH her OWN content "
    "library (call search_content, optionally get_post) AND the web (web_search) when outside "
    "context helps. Ground any claim about HER work in her content and cite her video titles; "
    "use the web for current or external facts and cite those sources. Be explicit about what "
    "comes from her content vs. the web, and say honestly if something isn't covered. Use the "
    "prior research notes if they're relevant."
)

TOOLS = [
    {
        "name": "search_content",
        "description": "Search Linda's own content (her posts/videos) by meaning. Returns the "
                       "most relevant items with post_id, title, snippet, url, and distance "
                       "(lower = closer).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "what to look for, in natural language"},
                "k": {"type": "integer", "description": "how many results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_post",
        "description": "Get the full text of one post by its post_id.",
        "input_schema": {
            "type": "object",
            "properties": {"post_id": {"type": "integer"}},
            "required": ["post_id"],
        },
    },
    # server-side web search — runs on Anthropic's side; combines external/current info
    {"type": "web_search_20260209", "name": "web_search"},
]


def _run_tool(conn, name, inp):
    if name == "search_content":
        return search_content(conn, inp["query"], int(inp.get("k", 5)))
    if name == "get_post":
        return get_post(conn, int(inp["post_id"]))
    return {"error": f"unknown tool {name}"}


def run_research(client, conn, question):
    """Answer `question` grounded in the content; record the run to memory. Returns (answer, sources)."""
    prior = recall(conn, question, k=3)
    prior_txt = "\n".join(f"- {m.get('detail') or ''}" for m in prior) if prior else "(no prior research yet)"
    facts = semantic_recall(conn, question, k=5)
    facts_txt = "\n".join(f"- [{f['category']}] {f['fact']}" for f in facts) if facts else "(none yet)"
    messages = [{
        "role": "user",
        "content": (f"Question about my content: {question}\n\n"
                    f"What I already know about my content (semantic memory):\n{facts_txt}\n\n"
                    f"Prior research notes (episodic):\n{prior_txt}"),
    }]

    sources = []   # (title, url) from HER content
    answer = ""
    while True:
        resp = client.messages.create(
            model=MODEL, max_tokens=4096, thinking={"type": "adaptive"},
            system=SYSTEM, tools=TOOLS, messages=messages,
        )
        # web_search is server-side; when it hits its loop limit, re-send to let it continue
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason != "tool_use":
            answer = "".join(b.text for b in resp.content if b.type == "text")
            break

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out = _run_tool(conn, b.name, b.input)
                if b.name == "search_content" and isinstance(out, list):
                    for r in out:
                        sources.append((r["title"], r["url"]))
                results.append({
                    "type": "tool_result", "tool_use_id": b.id,
                    "content": json.dumps(out, default=str),
                })
        messages.append({"role": "user", "content": results})

    # record this research run to episodic memory (so future questions build on it)
    uniq = list(dict.fromkeys(t for t, _ in sources))
    found = len(uniq) > 0
    detail = ("researched '" + question + "' -> sources: " + "; ".join(uniq[:5])) if found \
             else f"researched '{question}' -> no relevant content found"
    record(conn, "research", question, (answer[:500] or "(no answer)"),
           "research", "success" if found else "failure",
           reward=1.0 if found else 0.0, detail=detail)
    return answer, uniq
