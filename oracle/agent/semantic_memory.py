"""Semantic memory: consolidate EPISODIC memory (past research runs) into durable,
reusable FACTS, and recall them by meaning.

This is the DeepLearning.AI "memory-aware agents" move: an LLM pipeline that reads what
happened (episodic) and extracts consolidated knowledge (semantic) the agent can reuse
without re-deriving it. Facts are embedded in-DB and retrieved with vector search.
"""
import json

MODEL = "claude-opus-4-8"
EMBED_MODEL = "MINILM"

_SYS = (
    "You distill durable, reusable FACTS about a creator's content library from (a) the list "
    "of their content and (b) a log of past research over it. Output concise, standalone facts "
    "an assistant could reuse later — themes, recurring audience questions, formats, tools, and "
    "notable gaps. Deduplicate. Categories: theme | audience | format | tool | gap."
)

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"fact": {"type": "string"}, "category": {"type": "string"}},
        "required": ["fact", "category"],
    }}},
    "required": ["facts"],
}


def consolidate(client, conn, limit=30):
    """Read episodic memory + content, extract facts, (re)build semantic_memory. Returns facts."""
    cur = conn.cursor()
    cur.execute("select title from posts where title is not null")
    titles = [r[0] for r in cur.fetchall()]
    cur.execute("select task, action, detail from agent_memory order by created_at desc "
                f"fetch first {int(limit)} rows only")
    runs = cur.fetchall()

    prompt = (
        "CONTENT LIBRARY (titles):\n" + "\n".join(f"- {t}" for t in titles) +
        "\n\nPAST RESEARCH RUNS (question | answer-summary | notes):\n" +
        "\n".join(f"- {q} | {(a or '')[:160]} | {d or ''}" for q, a, d in runs) +
        "\n\nExtract the durable facts."
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=8192, system=_SYS,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    facts = json.loads(text)["facts"]

    # rebuild the consolidation snapshot
    cur.execute("delete from semantic_memory where source = 'consolidation'")
    for f in facts:
        cur.execute(
            "insert into semantic_memory (fact, category, source, embedding) "
            "values (:f, :c, 'consolidation', vector_embedding(" + EMBED_MODEL + " using :f as data))",
            f=f["fact"], c=f.get("category", ""),
        )
    conn.commit()
    return facts


def semantic_recall(conn, query, k=5):
    """Return the k most relevant consolidated facts for `query`."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT fact, category,
                   VECTOR_DISTANCE(embedding,
                                   VECTOR_EMBEDDING({EMBED_MODEL} USING :q AS DATA),
                                   COSINE) AS dist
            FROM   semantic_memory
            ORDER  BY dist
            FETCH  FIRST {int(k)} ROWS ONLY
            """,
            q=query,
        )
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
