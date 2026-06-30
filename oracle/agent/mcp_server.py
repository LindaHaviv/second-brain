"""MCP server over your second brain — so Claude Desktop (or any MCP client) can search,
read, and add to the brain over a local stdio connection. Everything stays on your machine.

Exposes the standard connector contract: search / fetch, plus wiki / topics (synthesized
knowledge pages), recent, and ingest_note.

Register in Claude Desktop (Settings -> Developer -> Edit Config), then restart Claude:
{
  "mcpServers": {
    "content-brain": {
      "command": "<repo>/.venv/bin/python",
      "args": ["<repo>/oracle/agent/mcp_server.py"]
    }
  }
}
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dotenv import load_dotenv
load_dotenv(HERE.parent / ".env")   # oracle/.env (DB creds) — explicit so it works from any cwd

import db                # noqa: E402
import content           # noqa: E402
from fastmcp import FastMCP   # noqa: E402

mcp = FastMCP("content-brain")


@mcp.tool
def search(query: str, k: int = 8) -> dict:
    """Search Linda's second brain (her videos, Shorts, AI chats, Notion ideas/scripts, and code
    sessions) by MEANING. Returns {"results": [{id, title, url, text}]} — the standard connector
    contract Claude and ChatGPT expect. Each result also carries `match` ("wiki" = a synthesized
    topic page, "item" = a post, "passage" = a chunk). Pass a result's `id` to fetch() for the
    full text."""
    conn = db.connect()
    try:
        results = []
        for r in content.search_content(conn, query, k):
            rid = f"wiki:{r['title']}" if r["lvl"] == "wiki" else str(r["post_id"])
            results.append({"id": rid, "title": r["title"] or "", "url": r["url"] or "",
                            "text": r["snippet"] or "", "source": r["platform_id"],
                            "match": r["lvl"]})
        return {"results": results}
    finally:
        conn.close()


@mcp.tool
def fetch(id: str) -> dict:
    """Fetch the full content of one search result by its `id`. Returns {id, title, text, url,
    metadata}. Handles both posts and wiki pages (ids like "wiki:<topic>")."""
    conn = db.connect()
    try:
        if isinstance(id, str) and id.startswith("wiki:"):
            page = content.get_wiki_page(conn, id[5:])
            if not page:
                return {"id": id, "title": "", "text": "not found", "url": "", "metadata": {}}
            return {"id": id, "title": page["topic"], "text": page["body"], "url": "",
                    "metadata": {"type": "wiki", "citations": len(page["citations"])}}
        post = content.get_post(conn, int(id))
        if not post:
            return {"id": str(id), "title": "", "text": "not found", "url": "", "metadata": {}}
        return {"id": str(id), "title": post.get("title") or "", "text": post.get("caption") or "",
                "url": post.get("url") or "",
                "metadata": {"type": post.get("kind"), "source": post.get("platform_id")}}
    finally:
        conn.close()


@mcp.tool
def wiki(topic: str) -> dict:
    """Fetch a compiled WIKI PAGE — a synthesized overview of everything in the brain about a
    topic, with citations back to the source content. Call this for a "wiki" search hit (its
    title is the topic), or to get Linda's synthesized take on a subject. topics() lists them."""
    conn = db.connect()
    try:
        return content.get_wiki_page(conn, topic) or {"error": "no page; try topics()"}
    finally:
        conn.close()


@mcp.tool
def topics() -> list:
    """List the compiled wiki topics — Linda's synthesized knowledge pages over her content."""
    conn = db.connect()
    try:
        return content.list_topics(conn)
    finally:
        conn.close()


@mcp.tool
def recent(k: int = 10) -> list:
    """The k most recently published items in the brain."""
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT post_id, platform_id, kind, title, url FROM posts "
                        "WHERE published_at IS NOT NULL ORDER BY published_at DESC "
                        "FETCH FIRST :k ROWS ONLY", k=k)
            cols = [c[0].lower() for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


@mcp.tool
def ingest_note(title: str, text: str) -> str:
    """Add a quick note/idea to the brain (embedded for future semantic search)."""
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("MERGE INTO platforms p USING (SELECT 'note' id FROM dual) s "
                        "ON (p.platform_id=s.id) WHEN NOT MATCHED THEN "
                        "INSERT (platform_id, display_name) VALUES ('note','Quick notes')")
            cur.execute(
                "INSERT INTO posts (platform_id, kind, title, caption, content_embedding) "
                "VALUES ('note','note', :t, :c, VECTOR_EMBEDDING(MINILM USING :e AS DATA))",
                t=title[:1000], c=text, e=f"{title}. {text}"[:3000])
        conn.commit()
        return f"saved note: {title}"
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()   # stdio transport (local)
