"""MCP server over your second brain — so Claude Desktop (or any MCP client) can search,
read, and add to the brain over a local stdio connection. Everything stays on your machine.

Exposes the standard connector contract: search / fetch (+ recent, ingest_note).

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
def search(query: str, k: int = 8) -> list:
    """Search Linda's second brain (her videos, Shorts, Claude chats, Notion ideas/scripts,
    brand deals, and code sessions) by MEANING. Returns ranked matches."""
    conn = db.connect()
    try:
        return [{"id": r["post_id"], "title": r["title"], "source": r["platform_id"],
                 "kind": r["kind"], "match": r["lvl"], "snippet": r["snippet"], "url": r["url"]}
                for r in content.search_content(conn, query, k)]
    finally:
        conn.close()


@mcp.tool
def fetch(id: int) -> dict:
    """Fetch the full content of one brain item by its id."""
    conn = db.connect()
    try:
        return content.get_post(conn, id) or {"error": "not found"}
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
