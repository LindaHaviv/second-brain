"""The research agent — Claude, grounded in YOUR content, with memory.

This ties the whole thesis together:
  • KNOWLEDGE  = your posts (semantic search via content.py)
  • MEMORY     = past research runs (agent_memory via memory.py)
  • both live in ONE Oracle database, retrieved together.

The agent searches your content, synthesizes a grounded answer, cites your videos,
and records what it found so future questions build on past research.
"""
import os
import json
import anthropic

from memory import record, recall
from content import search_hybrid, get_post, get_wiki_page
from semantic_memory import semantic_recall, consolidate

MODEL = "claude-opus-4-8"

SYSTEM = (
    "You are a research assistant for Linda. Research the question using BOTH her OWN content "
    "library (call search_content, optionally get_post) AND the web (web_search) when outside "
    "context helps. Her library also has compiled WIKI PAGES — synthesized overviews of a topic "
    "across all her work; when a search result's match level is 'wiki', read the full page with "
    "get_wiki_page(topic) and prefer it for a synthesized view of what she's covered (it cites the "
    "underlying posts). Ground any claim about HER work in her content and cite her video titles; "
    "use the web for current or external facts and cite those sources. Be explicit about what "
    "comes from her content vs. the web, and say honestly if something isn't covered. Use the "
    "prior research notes if they're relevant."
)

TOOLS = [
    {
        "name": "search_content",
        "description": "Search Linda's own content by meaning across three levels (see each "
                       "result's 'lvl'): 'wiki' = a synthesized topic page (read it fully with "
                       "get_wiki_page using its title), 'item' = a post, 'passage' = a chunk. "
                       "Returns post_id, title, snippet, url, lvl, and distance (lower = closer). "
                       "For 'wiki' results post_id is null — use get_wiki_page, not get_post.",
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
        "description": "Get the full text of one post by its post_id (for 'item'/'passage' hits).",
        "input_schema": {
            "type": "object",
            "properties": {"post_id": {"type": "integer"}},
            "required": ["post_id"],
        },
    },
    {
        "name": "get_wiki_page",
        "description": "Read a compiled WIKI PAGE: a synthesized overview of everything in Linda's "
                       "content about a topic, with citations back to the source posts. Pass the "
                       "topic (the title of a 'wiki' search result).",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    # server-side web search — runs on Anthropic's side; combines external/current info
    {"type": "web_search_20260209", "name": "web_search"},
]


def _maybe_consolidate(client, conn, every=None):
    """Auto-improve: once enough new research runs have accumulated since the last
    consolidation, re-distill episodic -> semantic so the agent's learned facts stay current.
    Best-effort — never let it break a research answer."""
    every = every or int(os.environ.get("CONSOLIDATE_EVERY", "5"))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM agent_memory WHERE created_at > "
                "NVL((SELECT MAX(created_at) FROM semantic_memory WHERE source = 'consolidation'),"
                " TIMESTAMP '2000-01-01 00:00:00')")
            new = cur.fetchone()[0]
        if new >= every:
            facts = consolidate(client, conn)
            print(f"[auto-consolidate] {new} new runs -> refreshed {len(facts)} semantic facts")
    except Exception:
        pass


def _run_tool(conn, name, inp):
    if name == "search_content":
        return search_hybrid(conn, inp["query"], int(inp.get("k", 5)))
    if name == "get_post":
        return get_post(conn, int(inp["post_id"]))
    if name == "get_wiki_page":
        return get_wiki_page(conn, inp["topic"])
    return {"error": f"unknown tool {name}"}


def run_research(client, conn, question, history=None):
    """Answer `question` grounded in the content; record the run to memory. Returns (answer, sources).
    `history` = prior conversation turns (working memory) for multi-turn follow-ups."""
    prior = recall(conn, question, k=3)
    prior_txt = "\n".join(f"- {m.get('detail') or ''}" for m in prior) if prior else "(no prior research yet)"
    facts = semantic_recall(conn, question, k=5)
    facts_txt = "\n".join(f"- [{f['category']}] {f['fact']}" for f in facts) if facts else "(none yet)"
    messages = list(history or [])   # conversational / working memory (prior turns this session)
    messages.append({
        "role": "user",
        "content": (f"Question about my content: {question}\n\n"
                    f"What I already know about my content (semantic memory):\n{facts_txt}\n\n"
                    f"Prior research notes (episodic):\n{prior_txt}"),
    })

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
                elif b.name == "get_wiki_page" and isinstance(out, dict) and out.get("topic"):
                    sources.append((f"wiki: {out['topic']}", None))
                    for cdict in out.get("citations", []):
                        sources.append((cdict["title"], cdict["url"]))
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
    _maybe_consolidate(client, conn)   # auto-improve: refresh semantic facts as runs accrue
    return answer, uniq
