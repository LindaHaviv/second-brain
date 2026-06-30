"""Conversational / working memory — the running dialogue of a session.

Best practice: persist the FULL history (durable, resumable), but load only a bounded
RECENT WINDOW into the model's context (working memory). This is what lets the agent handle
follow-ups like "which of those would be the best one?".
"""
import uuid


def new_session():
    return "sess-" + uuid.uuid4().hex[:10]


def record_turn(conn, session_id, role, content):
    with conn.cursor() as cur:
        cur.execute("SELECT NVL(MAX(seq), 0) + 1 FROM conversations WHERE session_id = :s",
                    s=session_id)
        seq = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO conversations (session_id, seq, role, content) "
            "VALUES (:s, :q, :r, :c)",
            s=session_id, q=seq, r=role, c=content,
        )
    conn.commit()


def recent_turns(conn, session_id, n=12):
    """The last n turns of the session, in chronological order (the working-memory window)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT role, content FROM (
              SELECT role, content, seq FROM conversations
              WHERE session_id = :s ORDER BY seq DESC FETCH FIRST :n ROWS ONLY
            ) ORDER BY seq
            """,
            s=session_id, n=n,
        )
        return [{"role": role, "content": content} for role, content in cur.fetchall()]
