"""Reconcile script DRAFTS with what actually got filmed and posted.

The scripting workflow saves shoot-ready drafts via ingest_note(kind='script'):
INTENT, like the Notion tracker rows reconcile_content.py closes the loop on. The
published-platform posts are REALITY. This step matches each active draft against
published posts so a filmed draft stops competing with the real thing in search:

  confirmed match -> report it; with --apply, link the draft to the live post (its
                     url column points at the published item) and archive it
                     (visibility='archived' — out of search/wiki/consolidation, the
                     published post carries the content from here on).
  possible match  -> report only, never auto-archived: a semantically close post
                     that the title can't confirm. The user decides.
  no match        -> the draft is still unfilmed; nothing to do, not reported.

Two deterministic signals per (draft, post) pair — no LLM:
  - title similarity (reconcile_content.score: sequence ratio / containment /
    distinctive-word overlap), with the 'Script: ' prefix stripped;
  - cosine distance between the STORED embeddings (a draft paraphrases the material
    its filmed post carries, so the vectors sit close even when the wording moved).

The report is saved into the brain only on --apply and only when something changed;
a dry run (the default) just prints.

Run:  ./.venv/bin/python scripts/reconcile_scripts.py [--apply]
"""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
sys.path.insert(0, str(ROOT / "scripts"))
import db  # noqa: E402
from reconcile_content import PLATFORMS, MATCH_THRESHOLD, score  # noqa: E402

NEIGHBORS = 8            # nearest published posts fetched per draft
CONFIRM_DIST = 0.25      # embedding distance at/below which a match confirms alone
POSSIBLE_DIST = 0.40     # ...at/below which a pair is worth reporting at all


def draft_topic(title):
    """The comparable part of a draft title: her save convention is 'Script: [topic]'."""
    t = (title or "").strip()
    return t.split(":", 1)[1].strip() if t.lower().startswith("script:") else t


def classify(title_score, dist):
    """-> 'confirmed' | 'possible' | None. Title agreement OR a very close embedding
    confirms; a merely-nearby embedding is surfaced for the user, never acted on."""
    if title_score >= MATCH_THRESHOLD or dist <= CONFIRM_DIST:
        return "confirmed"
    if dist <= POSSIBLE_DIST:
        return "possible"
    return None


def active_drafts(cur):
    cur.execute("SELECT post_id, title FROM posts WHERE platform_id='note' "
                "AND kind='script' AND NVL(visibility,'content')='content' "
                "ORDER BY post_id")
    return cur.fetchall()


def nearest_published(cur, draft_id, k=NEIGHBORS):
    """Published-platform posts nearest the draft's own stored embedding."""
    binds = ",".join(f":p{i}" for i in range(len(PLATFORMS)))
    cur.execute(
        f"""
        SELECT p2.post_id, p2.platform_id, p2.title, p2.url,
               TO_CHAR(p2.published_at,'YYYY-MM-DD') AS published,
               VECTOR_DISTANCE(p2.content_embedding, p1.content_embedding, COSINE) AS dist
        FROM   posts p1 JOIN posts p2 ON p2.post_id <> p1.post_id
        WHERE  p1.post_id = :id
          AND  p1.content_embedding IS NOT NULL AND p2.content_embedding IS NOT NULL
          AND  p2.platform_id IN ({binds})
          AND  NVL(p2.visibility,'content') = 'content'
        ORDER  BY dist
        FETCH FIRST {int(k)} ROWS ONLY
        """,
        dict({"id": int(draft_id)},
             **{f"p{i}": v for i, v in enumerate(sorted(PLATFORMS.values()))}))
    cols = [c[0].lower() for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def best_hit(cur, draft_id, title):
    """The single best (tier, hit, title_score) for one draft, or None."""
    topic = draft_topic(title)
    best = None
    for h in nearest_published(cur, draft_id):
        ts = score(topic, h["title"])
        tier = classify(ts, float(h["dist"]))
        if not tier:
            continue
        key = (tier == "confirmed", ts - float(h["dist"]))
        if best is None or key > best[0]:
            best = (key, tier, h, ts)
    return best and best[1:]


def archive_draft(cur, draft_id, url):
    """Link the filmed draft to its live post, then retire it from every read path."""
    cur.execute("UPDATE posts SET url = :u, visibility = 'archived' "
                "WHERE post_id = :id", u=(url or None), id=int(draft_id))


def main():
    apply = "--apply" in sys.argv
    conn = db.open_connection()
    cur = conn.cursor()
    filmed, possible = [], []
    for draft_id, title in active_drafts(cur):
        hit = best_hit(cur, draft_id, title)
        if not hit:
            continue
        tier, h, ts = hit
        line = (f"- \"{title}\" -> [{h['platform_id']}] {h['title']} "
                f"({h['published'] or '?'}) {h['url'] or ''} "
                f"[title {ts:.2f}, dist {float(h['dist']):.2f}]")
        if tier == "confirmed":
            if apply:
                archive_draft(cur, draft_id, h["url"])
            filmed.append(line)
        else:
            possible.append(line)
    if not filmed and not possible:
        print("reconcile_scripts: no active draft matches a published post — "
              "nothing to do")
        conn.close()
        return
    parts = []
    if filmed:
        parts.append(("FILMED (draft linked to the live post and archived):" if apply
                      else "FILMED (would link + archive — rerun with --apply):")
                     + "\n" + "\n".join(filmed))
    if possible:
        parts.append("POSSIBLY FILMED (close in meaning, title can't confirm — "
                     "review by hand):\n" + "\n".join(possible))
    today = datetime.date.today().isoformat()
    body = (f"Script reconciliation for {today} - which saved script drafts "
            f"match a published post:\n\n" + "\n\n".join(parts))
    print(body + ("" if apply else "\n\n(dry run: nothing written)"))
    if apply:
        conn.commit()
        if filmed:
            import freshness
            freshness.save_note(conn, f"Script reconciliation {today}", body)
            print("\n(saved to the brain)")
    conn.close()


if __name__ == "__main__":
    main()
