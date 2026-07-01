"""Karpathy wiki compiler — turn your content into self-maintaining, linked topic pages.

An LLM (1) proposes coherent topics from your content, then (2) compiles a synthesized page
per topic, citing the posts it used and linking to related topics. Pages are embedded and
stored in Oracle (wiki_pages + page_sources + page_links).

  python wiki.py            # full (re)build: propose topics + compile every page
  python wiki.py --refresh  # SELF-IMPROVING: recompile only topics with new content (cheap)

The refresh is what makes the layer self-improving: as you ingest more content, it updates
just the affected pages — no new content means no LLM calls at all.
"""
import json
import sys

import oracledb
import anthropic

from db import connect          # importing db loads oracle/.env (incl. ANTHROPIC_API_KEY)
from content import search_content

MODEL = "claude-opus-4-8"

TOPICS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"topics": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"topic": {"type": "string"}}, "required": ["topic"]}}},
    "required": ["topics"],
}
PAGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "body": {"type": "string"},
        "cited_post_ids": {"type": "array", "items": {"type": "integer"}},
        "links": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["body", "cited_post_ids", "links"],
}


def _json(client, system, prompt, schema, max_tokens=2048):
    r = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                               messages=[{"role": "user", "content": prompt}],
                               output_config={"format": {"type": "json_schema", "schema": schema}})
    return json.loads(next(b.text for b in r.content if b.type == "text"))


def propose_topics(client, conn, n=10):
    cur = conn.cursor()
    cur.execute("SELECT title FROM posts WHERE title IS NOT NULL "
                "AND NVL(visibility,'content') = 'content' "
                "AND platform_id IN ('youtube','notion') FETCH FIRST 200 ROWS ONLY")
    titles = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT fact FROM semantic_memory FETCH FIRST 30 ROWS ONLY")
    facts = [r[0] for r in cur.fetchall()]
    prompt = (f"Propose {n} coherent TOPIC pages that group this creator's recurring themes "
              "(for a personal knowledge wiki). Return concise topic names.\n\n"
              "CONTENT TITLES:\n" + "\n".join(f"- {t}" for t in titles[:200]) +
              "\n\nLEARNED FACTS:\n" + "\n".join(f"- {f}" for f in facts))
    return [t["topic"] for t in _json(client, "You organize a creator's content into wiki topics.",
                                      prompt, TOPICS_SCHEMA, 1024)["topics"]]


def compile_page(client, conn, topic, all_topics):
    """Synthesize one page from the content most relevant to `topic`. Returns (body, cites, links)."""
    hits = search_content(conn, topic, k=12)
    valid = {h["post_id"] for h in hits if h["post_id"]}
    src = "\n".join(f"[{h['post_id']}] ({h['platform_id']}/{h['kind']}) {h['title']}: "
                    f"{(h['snippet'] or '')[:160]}" for h in hits if h["post_id"])
    others = ", ".join(t for t in all_topics if t != topic)
    prompt = (f"Compile a concise wiki page for the topic \"{topic}\", synthesizing what THIS "
              f"creator has covered/said about it — grounded ONLY in the content below. Cite the "
              f"[post_id]s you actually used (cited_post_ids). From this list, pick related topics "
              f"to link: {others}.\n\nCONTENT:\n{src}")
    d = _json(client, "You compile grounded, synthesized wiki pages from a creator's own content.",
              prompt, PAGE_SCHEMA, 2048)
    cites = [i for i in d.get("cited_post_ids", []) if i in valid]
    return d["body"], cites, d.get("links", [])


# --- storage helpers -------------------------------------------------------------------------

def _insert_page(cur, topic, body):
    outid = cur.var(oracledb.NUMBER)
    cur.execute("INSERT INTO wiki_pages (topic, body, embedding) "
                "VALUES (:t, :b, VECTOR_EMBEDDING(MINILM USING :e AS DATA)) "
                "RETURNING page_id INTO :o",
                t=topic[:200], b=body, e=f"{topic}. {body}"[:3000], o=outid)
    return int(outid.getvalue()[0])


def _update_page(cur, page_id, topic, body):
    cur.execute("UPDATE wiki_pages SET body = :b, "
                "embedding = VECTOR_EMBEDDING(MINILM USING :e AS DATA), updated_at = SYSTIMESTAMP "
                "WHERE page_id = :p", b=body, e=f"{topic}. {body}"[:3000], p=page_id)


def _set_citations(cur, page_id, cites):
    cur.execute("DELETE FROM page_sources WHERE page_id = :p", p=page_id)
    for post_id in cites:
        try:
            cur.execute("INSERT INTO page_sources (page_id, post_id) VALUES (:p, :s)",
                        p=page_id, s=int(post_id))
        except oracledb.DatabaseError:
            pass


def _set_links(cur, page_id, link_names, name2id):
    cur.execute("DELETE FROM page_links WHERE from_page_id = :p", p=page_id)
    for lk in link_names:
        tid = name2id.get((lk or "").lower())
        if tid and tid != page_id:
            try:
                cur.execute("INSERT INTO page_links (from_page_id, to_page_id) VALUES (:f, :t)",
                            f=page_id, t=tid)
            except oracledb.DatabaseError:
                pass


def _max_post_id(cur):
    cur.execute("SELECT NVL(MAX(post_id), 0) FROM posts")
    return int(cur.fetchone()[0])


def _set_hwm(cur, value):
    cur.execute("UPDATE wiki_meta SET last_max_post_id = :v, refreshed_at = SYSTIMESTAMP "
                "WHERE id = 1", v=value)


def _get_hwm(cur):
    cur.execute("SELECT last_max_post_id FROM wiki_meta WHERE id = 1")
    row = cur.fetchone()
    return int(row[0]) if row else 0


# --- build (full) and refresh (incremental) --------------------------------------------------

def build_wiki(client, conn, n=10):
    cur = conn.cursor()
    cur.execute("DELETE FROM wiki_pages")   # cascades to links + sources
    conn.commit()
    topics = propose_topics(client, conn, n)
    name2id, link_plan = {}, {}
    for topic in topics:
        body, cites, links = compile_page(client, conn, topic, topics)
        pid = _insert_page(cur, topic, body)
        name2id[topic.lower()] = pid
        link_plan[pid] = links
        _set_citations(cur, pid, cites)
        conn.commit()
        print(f"  compiled '{topic}'  ({len(cites)} citations)")
    for pid, links in link_plan.items():
        _set_links(cur, pid, links, name2id)
    _set_hwm(cur, _max_post_id(cur))
    conn.commit()
    print(f"built {len(topics)} wiki pages")


def refresh_wiki(client, conn):
    """Recompile only topics whose top retrieval now includes posts newer than the last
    compile. No new content -> no LLM calls."""
    cur = conn.cursor()
    hwm = _get_hwm(cur)
    cur_max = _max_post_id(cur)
    if cur_max <= hwm:
        print(f"no new content since last compile (max post_id {cur_max}); nothing to refresh")
        return
    new_ids = set()
    cur.execute("SELECT post_id FROM posts WHERE post_id > :h "
                "AND NVL(visibility,'content') = 'content'", h=hwm)
    new_ids = {int(r[0]) for r in cur.fetchall()}
    print(f"{len(new_ids)} new posts since last compile — checking which topics they touch")

    cur.execute("SELECT page_id, topic FROM wiki_pages")
    existing = cur.fetchall()
    name2id = {t.lower(): pid for pid, t in existing}
    topics = [t for _, t in existing]
    link_plan, refreshed, skipped = {}, [], []
    for pid, topic in existing:
        hits = search_content(conn, topic, k=12)
        touched = {h["post_id"] for h in hits if h["post_id"]} & new_ids
        if not touched:
            skipped.append(topic)
            continue
        body, cites, links = compile_page(client, conn, topic, topics)
        _update_page(cur, pid, topic, body)
        _set_citations(cur, pid, cites)
        link_plan[pid] = links
        refreshed.append(topic)
        conn.commit()
        print(f"  refreshed '{topic}'  ({len(cites)} citations)")
    for pid, links in link_plan.items():
        _set_links(cur, pid, links, name2id)
    _set_hwm(cur, cur_max)
    conn.commit()
    print(f"refresh done: {len(refreshed)} refreshed, {len(skipped)} unchanged")


def main():
    client = anthropic.Anthropic()
    conn = connect()
    try:
        if "--refresh" in sys.argv:
            refresh_wiki(client, conn)
        else:
            build_wiki(client, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
