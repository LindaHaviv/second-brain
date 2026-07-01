"""Semantic search over YOUR content (posts), powered by in-DB embeddings.

This is the 'knowledge' the research agent reasons over — distinct from agent_memory
(its 'experience'). Both live in the same Oracle database.
"""

import re

EMBED_MODEL = "MINILM"


def search_content(conn, query, k=5):
    """Return the k most semantically relevant matches, by meaning — across THREE levels,
    ranked together by distance:
      - 'wiki'    : a compiled topic page (synthesized knowledge over your content)
      - 'item'    : a post overview
      - 'passage' : a specific chunk inside a long chat/transcript
    So a query can land on the synthesized page, the right post, AND the exact passage.
    Wiki hits carry the topic in `title` (and post_id IS NULL) — fetch the full page with
    get_wiki_page(topic)."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT * FROM (
              SELECT post_id, platform_id, kind, title, SUBSTR(caption, 1, 400) AS snippet, url,
                     series, 'item' AS lvl,
                     VECTOR_DISTANCE(content_embedding,
                                     VECTOR_EMBEDDING({EMBED_MODEL} USING :q AS DATA), COSINE) AS dist
              FROM   posts
              WHERE  content_embedding IS NOT NULL AND NVL(visibility,'content') = 'content'
              UNION ALL
              SELECT p.post_id, p.platform_id, p.kind, p.title, SUBSTR(ch.chunk, 1, 400) AS snippet,
                     p.url, p.series, 'passage' AS lvl,
                     VECTOR_DISTANCE(ch.embedding,
                                     VECTOR_EMBEDDING({EMBED_MODEL} USING :q AS DATA), COSINE) AS dist
              FROM   content_chunks ch JOIN posts p ON p.post_id = ch.post_id
              WHERE  NVL(p.visibility,'content') = 'content'
              UNION ALL
              SELECT NULL AS post_id, 'wiki' AS platform_id, 'page' AS kind, topic AS title,
                     SUBSTR(body, 1, 400) AS snippet, NULL AS url, NULL AS series, 'wiki' AS lvl,
                     VECTOR_DISTANCE(embedding,
                                     VECTOR_EMBEDDING({EMBED_MODEL} USING :q AS DATA), COSINE) AS dist
              FROM   wiki_pages
              WHERE  embedding IS NOT NULL
            )
            ORDER BY dist
            FETCH FIRST {int(k)} ROWS ONLY
            """,
            q=query,
        )
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _terms(query):
    return [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2][:8]


def _lexical_posts(conn, terms, k):
    """Keyword search over posts (title + caption), ranked by how many query terms match.
    Catches exact names/handles/error codes that pure-vector search can miss."""
    if not terms:
        return []
    score = " + ".join(f"(CASE WHEN UPPER(NVL(title,' ')||' '||caption) LIKE :t{i} "
                       f"THEN 1 ELSE 0 END)" for i in range(len(terms)))
    where = " OR ".join(f"UPPER(NVL(title,' ')||' '||caption) LIKE :t{i}" for i in range(len(terms)))
    sql = (f"SELECT post_id, platform_id, kind, title, SUBSTR(caption,1,400) AS snippet, url, "
           f"series, 'item' AS lvl FROM posts WHERE caption IS NOT NULL "
           f"AND NVL(visibility,'content') = 'content' AND ({where}) "
           f"ORDER BY ({score}) DESC, post_id FETCH FIRST {int(k)} ROWS ONLY")
    binds = {f"t{i}": f"%{terms[i].upper()}%" for i in range(len(terms))}
    with conn.cursor() as cur:
        cur.execute(sql, **binds)
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _rid(r):
    return f"wiki:{r['title']}" if r.get("lvl") == "wiki" else f"{r['lvl']}:{r.get('post_id')}"


def search_hybrid(conn, query, k=8, C=60):
    """Hybrid retrieval: fuse semantic (vector) and keyword (lexical) results with Reciprocal
    Rank Fusion. Vector handles meaning; lexical rescues exact tokens. Returns the same row
    shape as search_content."""
    pool = max(k * 3, 20)
    vec = search_content(conn, query, pool)
    lex = _lexical_posts(conn, _terms(query), pool)
    scores, meta, retr = {}, {}, {}
    for rank, r in enumerate(vec):
        rid = _rid(r)
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (C + rank)
        meta[rid] = r
        retr.setdefault(rid, set()).add("semantic")
    for rank, r in enumerate(lex):
        rid = _rid(r)
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (C + rank)
        meta.setdefault(rid, r)
        retr.setdefault(rid, set()).add("keyword")
    ranked = sorted(scores, key=lambda x: scores[x], reverse=True)[:k]
    out = []
    for i, rid in enumerate(ranked, 1):
        row = dict(meta[rid])          # attach the fusion trace (educational + honest)
        row["rank"] = i
        row["rrf_score"] = round(scores[rid], 4)
        row["retrievers"] = sorted(retr[rid])   # ['semantic'], ['keyword'], or both
        out.append(row)
    return out


def get_wiki_page(conn, topic):
    """Full compiled wiki page for a topic, with its citations back to your content.
    Returns {topic, body, citations:[{post_id,title,platform,url}]} or None."""
    with conn.cursor() as cur:
        cur.execute("SELECT page_id, topic, body FROM wiki_pages WHERE topic = :t", t=topic)
        row = cur.fetchone()
        if not row:
            return None
        page_id, topic, body = row
        cur.execute(
            "SELECT p.post_id, p.title, p.platform_id, p.url "
            "FROM page_sources ps JOIN posts p ON p.post_id = ps.post_id "
            "WHERE ps.page_id = :p", p=page_id)
        cites = [{"post_id": r[0], "title": r[1], "platform": r[2], "url": r[3]}
                 for r in cur.fetchall()]
        return {"topic": topic, "body": body, "citations": cites}


def list_topics(conn):
    """The compiled wiki topics (titles only)."""
    with conn.cursor() as cur:
        cur.execute("SELECT topic FROM wiki_pages ORDER BY topic")
        return [r[0] for r in cur.fetchall()]


def list_series(conn):
    """The content series present in the brain (e.g. 'tech_walk') with a count each."""
    with conn.cursor() as cur:
        cur.execute("SELECT series, COUNT(*) AS n FROM posts WHERE series IS NOT NULL "
                    "AND NVL(visibility,'content')='content' GROUP BY series ORDER BY n DESC")
        return [{"series": r[0], "count": int(r[1])} for r in cur.fetchall()]


def list_by_series(conn, series, k=25):
    """List items in a content series (e.g. 'tech_walk'), most recent first."""
    with conn.cursor() as cur:
        cur.execute("SELECT post_id, platform_id, kind, title, url, "
                    "TO_CHAR(published_at,'YYYY-MM-DD') AS published FROM posts "
                    "WHERE series = :s AND NVL(visibility,'content')='content' "
                    "ORDER BY published_at DESC NULLS LAST FETCH FIRST :k ROWS ONLY",
                    s=series, k=int(k))
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_post(conn, post_id):
    """Full content of one post (for the agent to read in detail)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT post_id, platform_id, kind, title, caption, url, published_at, views "
            "FROM posts WHERE post_id = :id AND NVL(visibility,'content') = 'content'", id=post_id)
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0].lower() for c in cur.description]
        return dict(zip(cols, row))
