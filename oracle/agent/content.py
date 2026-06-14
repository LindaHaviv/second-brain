"""Semantic search over YOUR content (posts), powered by in-DB embeddings.

This is the 'knowledge' the research agent reasons over — distinct from agent_memory
(its 'experience'). Both live in the same Oracle database.
"""

EMBED_MODEL = "MINILM"


def search_content(conn, query, k=5):
    """Return the k posts most semantically relevant to `query`, by meaning."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT post_id, platform_id, kind, title, SUBSTR(caption, 1, 400) AS snippet, url,
                   VECTOR_DISTANCE(content_embedding,
                                   VECTOR_EMBEDDING({EMBED_MODEL} USING :q AS DATA),
                                   COSINE) AS dist
            FROM   posts
            WHERE  content_embedding IS NOT NULL
            ORDER  BY dist
            FETCH  FIRST {int(k)} ROWS ONLY
            """,
            q=query,
        )
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_post(conn, post_id):
    """Full content of one post (for the agent to read in detail)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT post_id, platform_id, kind, title, caption, url, published_at, views "
            "FROM posts WHERE post_id = :id", id=post_id)
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0].lower() for c in cur.description]
        return dict(zip(cols, row))
