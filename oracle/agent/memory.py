"""Agent memory: write experiences + recall them by meaning.

The embedding is generated INSIDE Oracle via VECTOR_EMBEDDING(MINILM ...) — no
external embedding API. Relational columns make outcomes auditable with plain SQL.
"""

EMBED_MODEL = "MINILM"  # the in-DB ONNX model loaded by setup/01_load_onnx_model.sql


def record(conn, run_id, task, action, tool, outcome, reward=None, detail=None):
    """Write one episodic memory row, embedding the experience text in-DB.

    Values are clamped to their column sizes so a long question can never
    crash the save step after an expensive run (ORA-12899).
    """
    experience = f"{task} | {action} | {detail or ''}"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO agent_memory
                  (run_id, task, action, tool, outcome, reward, detail, embedding)
            VALUES (:run_id, :task, :action, :tool, :outcome, :reward, :detail,
                    VECTOR_EMBEDDING({EMBED_MODEL} USING :exp AS DATA))
            """,
            run_id=(run_id or "")[:40], task=(task or "")[:500],
            action=action, tool=tool[:80] if tool else None,
            outcome=(outcome or "")[:10], reward=reward, detail=detail,
            exp=experience,
        )
    conn.commit()


def recall(conn, query, k=5):
    """Return the k most semantically-relevant past experiences for `query`."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT task, action, outcome, reward, detail,
                   VECTOR_DISTANCE(embedding,
                                   VECTOR_EMBEDDING({EMBED_MODEL} USING :q AS DATA),
                                   COSINE) AS dist
            FROM   agent_memory
            ORDER  BY dist
            FETCH  FIRST {int(k)} ROWS ONLY
            """,
            q=query,
        )
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def tool_stats(conn):
    """The auditable flex: the agent's track record per tool, plain SQL."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tool, attempts, successes, success_rate "
            "FROM tool_stats ORDER BY success_rate DESC"
        )
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
