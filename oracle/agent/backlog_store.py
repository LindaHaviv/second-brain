"""Backlog storage — the DB side of the prioritization loop.

`backlog_core` is pure (rank a list of Items); this module is the only place backlog
items touch the database. The contract mirrors the original file round-trip: `load()`
returns every item (active + done, insertion order), `save()` replaces the whole set in
one transaction — small N, and identical semantics to rewriting the file, so the engine
and its tests don't care which store is underneath.

The table (schema/10_backlog.sql) lives beside the brain but is invisible to it: search,
the wiki compiler, and memory consolidation never read it. Reachability is the backlog
CLI and the (auth-gated) backlog MCP tools only.

BACKLOG_TABLE overrides the table name (tests use a throwaway table on the same DB).
"""
import datetime
import os

import db
from backlog_core import EFFORTS, TYPES, Item


def _table():
    # identifier, not a bind var — restrict to a safe charset
    t = os.environ.get("BACKLOG_TABLE", "backlog_items")
    if not t.replace("_", "").isalnum():
        raise ValueError(f"bad BACKLOG_TABLE: {t!r}")
    return t


def _iso(d):
    return d.date().isoformat() if isinstance(d, datetime.datetime) else (
        d.isoformat() if isinstance(d, datetime.date) else None)


def _date(s):
    try:
        return datetime.date.fromisoformat(s.strip()) if s else None
    except ValueError:
        return None   # tolerate a hand-entered 'TBD' the same way the engine does


def load(conn=None) -> list[Item]:
    """Every item, insertion order (item_id), done included — same shape parse_items gave."""
    own = conn is None
    conn = conn or db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT title, item_type, strategic, deadline, effort, since_d, "
                        f"next_action, done FROM {_table()} ORDER BY item_id")
            rows = cur.fetchall()
        out = []
        for title, typ, strat, deadline, effort, since, nxt, done in rows:
            typ = typ if typ in TYPES else "idea"
            effort = effort if effort in EFFORTS else None
            out.append(Item(title=title, type=typ, strategic=bool(strat),
                            deadline=_iso(deadline), effort=effort, since=_iso(since),
                            next_action=nxt or "", done=bool(done)))
        return out
    finally:
        if own:
            conn.close()


def save(items: list[Item], conn=None):
    """Replace the whole set in ONE transaction (delete + insert, ~dozens of rows).
    Matches the file-rewrite semantics exactly; item order is preserved via insert order."""
    own = conn is None
    conn = conn or db.connect()
    try:
        with conn.cursor() as cur:
            # Autonomous enables parallel DML by default; a delete+insert cycle then
            # self-deadlocks (ORA-12860). Same guard the ingest tools use.
            cur.execute("alter session disable parallel dml")
            cur.execute(f"DELETE FROM {_table()}")
            cur.executemany(
                f"INSERT INTO {_table()} (title, item_type, strategic, deadline, effort, "
                f"since_d, next_action, done) VALUES (:1, :2, :3, "
                f"TO_DATE(:4, 'YYYY-MM-DD'), :5, TO_DATE(:6, 'YYYY-MM-DD'), :7, :8)",
                [(it.title[:400], it.type if it.type in TYPES else "idea",
                  int(bool(it.strategic)),
                  _iso(_date(it.deadline)), it.effort if it.effort in EFFORTS else None,
                  _iso(_date(it.since)), (it.next_action or "")[:1000], int(bool(it.done)))
                 for it in items])
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if own:
            conn.close()
