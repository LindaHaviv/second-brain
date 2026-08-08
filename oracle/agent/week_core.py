"""Said-vs-did — the pure logic of the weekly review question.

Given what the week was SUPPOSED to be about (the ranked top items, as titles)
and what verifiably HAPPENED (shipped posts, items closed), line them up and
say which promises moved and what the week actually went to. Deliberately
tiny and dependency-free: the caller does the I/O (a backlog snapshot from
git history, a published-posts query, anything that yields titles); this
module owns only the comparison, so it can be unit-tested and never drifts.

Matching is the proven three-signal title matcher (sequence ratio, containment,
distinctive-word overlap — a planned title and a posted caption often share the
nouns but not the phrasing), refusing weak matches rather than flattering the
week. The verdicts:

  done       — a did-title matched: the promise verifiably moved.
  untouched  — nothing matched: the promise sat.

Everything that happened but matched no promise is returned as `unplanned` —
where the week actually went. Divergence is an observation, not a scolding:
if the unplanned list keeps winning, the list is wrong, not the week.

Shared by: private weekly-review agents (the personal layer adds the snapshot
I/O, the shipped-work queries, and reporting); tests/test_week.py pins the
matcher and both verdicts.
"""
import difflib
import re

MATCH_THRESHOLD = 0.55   # below this, a match is refused (untouched beats flattery)


def normalize(s):
    """Lowercase, strip everything but word chars/spaces, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def score(a, b):
    """Similarity of two titles in [0,1]. Three signals, best wins: sequence ratio,
    containment (one title embedded in the other), and distinctive-word overlap."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    if len(na) >= 12 and len(nb) >= 12 and (na in nb or nb in na):
        ratio = max(ratio, 0.85)
    wa = {w for w in na.split() if len(w) >= 4}
    wb = {w for w in nb.split() if len(w) >= 4}
    if len(wa) >= 3 and len(wb) >= 3:
        overlap = len(wa & wb) / min(len(wa), len(wb))
        if overlap >= 0.5:
            ratio = max(ratio, 0.55 + 0.3 * overlap)
    return ratio


def compare(said, did, threshold=MATCH_THRESHOLD):
    """said: titles the week was supposed to be about, in priority order.
    did: titles of things that verifiably happened.
    Returns {"verdicts": [{"said", "status", "evidence"}...], "unplanned": [...]}
    — verdicts in said order; unplanned in did order."""
    verdicts, used = [], set()
    for s in said:
        best_i, best_s = None, threshold
        for i, d in enumerate(did):
            sc = score(s, d)
            if sc > best_s:
                best_i, best_s = i, sc
        if best_i is None:
            verdicts.append({"said": s, "status": "untouched", "evidence": None})
        else:
            used.add(best_i)
            verdicts.append({"said": s, "status": "done", "evidence": did[best_i]})
    unplanned = [d for i, d in enumerate(did) if i not in used]
    return {"verdicts": verdicts, "unplanned": unplanned}
