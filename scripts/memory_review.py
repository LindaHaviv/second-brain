"""Memory hygiene review — the FORGETTING report. Report-only: it never deletes.

  ./.venv/bin/python scripts/memory_review.py            # human-readable report
  ./.venv/bin/python scripts/memory_review.py --json     # machine-readable (for the
                                                          # weekly loop-health note)
  ./.venv/bin/python scripts/memory_review.py --write    # also persist the JSON to
                                                          # exports/memory_review.json
                                                          # (the scheduled sync mode)

A memory store that only grows drifts toward noise: time-bound facts go stale
("preparing the launch" is false a month later), near-duplicates pile up, and every
extra row makes recall a little worse. Mature agent-memory practice (Letta's
sleep-time compute, Mem0's dedup/conflict pipeline, Anthropic's memory curation)
treats FORGETTING as a designed stage of the loop. This is that stage's audit:

  1. STALE-BY-LANGUAGE — old memories written in present/future tense about
     time-bound states (structural regex, no LLM). Old durable facts are fine;
     old "currently working on X" facts are lies with a timestamp.
  2. NEAR-DUPLICATES — memory pairs whose embeddings sit closer than the
     threshold (in-database VECTOR_DISTANCE; custom track only — the OAMP
     package dedupes at extraction time).
  3. VOLUME — per-store counts and growth, so "memory is getting noisy" is a
     number, not a feeling.
  4. CONVERSATION SIGNALS — what the dialogue itself says should improve (plain
     SQL over content-scope rows, no LLM): questions asked again and again are
     wiki-page/fact candidates (synthesize once instead of re-deriving every
     time); failed runs mark where recall or retrieval let a question down.
  5. LOOP HEALTH — sync steps that FAILED in every one of the last few runs
     (from exports/sync_status.json), headlined at the top of the report. The
     sync deliberately continues past a failed step, so without this a broken
     loader can fail quietly for weeks. Deliberate skips (an unconfigured
     source) are not escalated — a known skip alarming daily would train you
     to ignore the report.

Review the report, then retire rows by hand (SQL) or via the package's lifecycle
API. When the same finding shows up run after run, that's the signal to automate
that specific retirement — not before. (Deliberately conservative: deleting
memories is the one loop that should never run unattended first.)
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "oracle" / "agent"))
import oracledb  # noqa: E402

import db  # noqa: E402

oracledb.defaults.fetch_lobs = False

# present/future-tense, time-bound markers: a memory OLDER than STALE_AFTER_DAYS that
# still "speaks in the present" about a temporary state is a staleness candidate.
TEMPORAL = re.compile(
    r"\b(currently|right now|this (week|month|quarter)|next (week|month)|upcoming|"
    r"in progress|is preparing|is planning|is working on|recently|soon|about to|"
    r"not yet|today|tomorrow)\b", re.I)
STALE_AFTER_DAYS = 60
DUP_DISTANCE = 0.15          # cosine distance below this = near-duplicate pair


def is_stale_candidate(text, age_days, stale_after=STALE_AFTER_DAYS):
    """Pure + unit-tested: old enough AND written in time-bound present tense."""
    return age_days >= stale_after and bool(TEMPORAL.search(text or ""))


REPEAT_MIN = 3               # same question this many times = synthesis candidate
SIGNAL_WINDOW_DAYS = 30      # how far back the conversation-signal scan looks


def normalize_question(text):
    """Pure + unit-tested: collapse a question to a comparison key — lowercase,
    punctuation stripped, whitespace squeezed — so trivial rewordings group together."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (text or "").lower())).strip()


FAIL_STREAK = 3              # a step failing this many consecutive runs headlines


def failing_streaks(history, min_runs=FAIL_STREAK):
    """Pure + unit-tested: steps whose status is 'fail' in EVERY one of the last
    `min_runs` sync runs. Fails only — a deliberate skip (unconfigured source) repeating
    forever is expected, and escalating it would bury the real alarms."""
    if len(history) < min_runs:
        return []
    recent = history[-min_runs:]
    out = []
    for step in recent[-1].get("steps", []):
        label = step.get("label")
        statuses = [next((s.get("status") for s in run.get("steps", [])
                          if s.get("label") == label), None) for run in recent]
        if all(st == "fail" for st in statuses):
            out.append({"step": label, "consecutive_fails": min_runs,
                        "last_run": recent[-1].get("run_at", "")[:16]})
    return out


def review(conn):
    cur = conn.cursor()
    out = {"stale_candidates": [], "duplicate_pairs": [], "counts": {},
           "repeated_questions": [], "failed_runs": [], "failing_loops": []}

    # --- volume, both stores (whichever exist) ---
    for table, col in (("semantic_memory", "fact"), ("brain_memory", "content")):
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            out["counts"][table] = cur.fetchone()[0]
        except oracledb.DatabaseError:
            continue   # store not present on this backend/schema

    # --- stale-by-language, both stores ---
    for table, idcol, col, when in (
            ("semantic_memory", "fact_id", "fact", "created_at"),
            ("brain_memory", "rowid", "content", "created_at")):
        if table not in out["counts"]:
            continue
        try:
            cur.execute(f"SELECT {idcol}, {col}, "
                        f"TRUNC(SYSDATE - CAST({when} AS DATE)) FROM {table}")
            for rid, text, age in cur.fetchall():
                if is_stale_candidate(str(text or ""), int(age or 0)):
                    out["stale_candidates"].append(
                        {"store": table, "id": str(rid), "age_days": int(age),
                         "text": str(text)[:180]})
        except oracledb.DatabaseError:
            continue

    # --- near-duplicates (custom store; embeddings are ours to query) ---
    if out["counts"].get("semantic_memory"):
        try:
            cur.execute("""
                SELECT a.fact_id, b.fact_id, a.fact, b.fact,
                       VECTOR_DISTANCE(a.embedding, b.embedding, COSINE) d
                FROM semantic_memory a JOIN semantic_memory b ON a.fact_id < b.fact_id
                WHERE VECTOR_DISTANCE(a.embedding, b.embedding, COSINE) < :thr
                ORDER BY d FETCH FIRST 25 ROWS ONLY""", thr=DUP_DISTANCE)
            for ida, idb, fa, fb, d in cur.fetchall():
                out["duplicate_pairs"].append(
                    {"ids": [int(ida), int(idb)], "distance": round(float(d), 3),
                     "a": str(fa)[:120], "b": str(fb)[:120]})
        except oracledb.DatabaseError:
            pass

    # --- conversation signals: what the dialogue says should improve (content-scope) ---
    try:
        cur.execute("""
            SELECT content FROM conversations
            WHERE role = 'user' AND NVL(visibility,'content') = 'content'
              AND created_at > SYSTIMESTAMP - NUMTODSINTERVAL(:d, 'DAY')""",
            d=SIGNAL_WINDOW_DAYS)
        groups = {}
        for (content,) in cur.fetchall():
            key = normalize_question(str(content or ""))[:200]
            if len(key) < 10:      # too short to mean anything ("yes", "ok", "more")
                continue
            groups.setdefault(key, []).append(str(content)[:180])
        for key, asks in groups.items():
            if len(asks) >= REPEAT_MIN:
                out["repeated_questions"].append(
                    {"times": len(asks), "question": asks[0]})
        out["repeated_questions"].sort(key=lambda r: -r["times"])
    except oracledb.DatabaseError:
        pass
    try:
        cur.execute("""
            SELECT task, tool, TO_CHAR(created_at,'YYYY-MM-DD') FROM agent_memory
            WHERE outcome = 'failure' AND NVL(visibility,'content') = 'content'
              AND created_at > SYSTIMESTAMP - NUMTODSINTERVAL(:d, 'DAY')
            ORDER BY created_at DESC FETCH FIRST 25 ROWS ONLY""",
            d=SIGNAL_WINDOW_DAYS)
        out["failed_runs"] = [{"task": str(t or "")[:180], "tool": tool, "when": when}
                              for t, tool, when in cur.fetchall()]
    except oracledb.DatabaseError:
        pass

    # --- loop health: steps failing run after run, from the sync's own status ledger ---
    try:
        status = pathlib.Path(__file__).resolve().parent.parent / "exports" / "sync_status.json"
        history = json.loads(status.read_text()) if status.exists() else []
        out["failing_loops"] = failing_streaks(history)
    except Exception:
        out["failing_loops"] = []   # a malformed ledger must not sink the whole report
    return out


def main():
    conn = db.connect()
    try:
        out = review(conn)
    finally:
        conn.close()
    if "--write" in sys.argv:
        # the scheduled mode: persist for the weekly loop-health flow (and anything
        # else that reads exports/), then fall through to the human-readable report
        # so the sync log shows the findings too.
        path = pathlib.Path(__file__).resolve().parent.parent / "exports" / "memory_review.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=1))
        print(f"(report written to {path.relative_to(path.parent.parent)})")
    if "--json" in sys.argv:
        print(json.dumps(out, indent=1))
        return
    print("MEMORY HYGIENE REVIEW (report-only — nothing was deleted)\n")
    if out["failing_loops"]:
        # the loudest thing first: a loop that keeps failing outranks every hygiene finding
        print(f"!! LOOPS FAILING {FAIL_STREAK}+ CONSECUTIVE RUNS — fix these first:")
        for f in out["failing_loops"]:
            print(f"  {f['step']} (last: {f['last_run']})")
        print()
    print("counts:", ", ".join(f"{k}={v}" for k, v in out["counts"].items()) or "no stores found")
    print(f"\nstale candidates (>= {STALE_AFTER_DAYS}d old, time-bound language): "
          f"{len(out['stale_candidates'])}")
    for s in out["stale_candidates"][:15]:
        print(f"  [{s['store']} {s['id']}] {s['age_days']}d: {s['text']}")
    print(f"\nnear-duplicate pairs (cosine < {DUP_DISTANCE}): {len(out['duplicate_pairs'])}")
    for p in out["duplicate_pairs"][:10]:
        print(f"  d={p['distance']}  #{p['ids'][0]} ~ #{p['ids'][1]}")
        print(f"    a: {p['a']}\n    b: {p['b']}")
    print(f"\nrepeated questions (>= {REPEAT_MIN}x in {SIGNAL_WINDOW_DAYS}d — "
          f"wiki/fact candidates): {len(out['repeated_questions'])}")
    for r in out["repeated_questions"][:10]:
        print(f"  {r['times']}x: {r['question']}")
    print(f"\nfailed runs (last {SIGNAL_WINDOW_DAYS}d — where recall/retrieval "
          f"let a question down): {len(out['failed_runs'])}")
    for f in out["failed_runs"][:10]:
        print(f"  {f['when']} [{f['tool'] or '-'}] {f['task']}")
    if not any((out["stale_candidates"], out["duplicate_pairs"],
                out["repeated_questions"], out["failed_runs"])):
        print("\nclean — nothing to retire or improve this pass.")


if __name__ == "__main__":
    main()
