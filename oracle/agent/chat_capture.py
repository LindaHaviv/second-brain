"""Chat capture selection — what belongs in the brain, and what must never enter it.

Sweeping conversations into a second brain is only an improvement over a raw
export if something decides what NOT to keep. An export gives you everything,
including the medical question and the furniture shopping; a sweep should give
you the working record and nothing else.

Two gates, in order, and the order matters:

  1. PRIVATE-BY-POLICY — the deny-list the memory layer already enforces
     (deal terms, rates, contracts, account numbers). In memory these are
     quarantined; in a chat sweep they are SKIPPED outright, because the
     deployment rule is that business/deal data never reaches the cloud brain
     at all. Quarantining still stores it.
  2. PERSONAL-LIFE — health, family logistics, shopping, travel admin,
     household. Nothing is wrong with these conversations; they simply are not
     the working record, and a brain that answers "what have I said about X"
     is degraded by them.

Everything else is the keep set: craft, technical work, decisions, research,
system design.

`should_capture` is pure so the boundary can be unit-tested rather than trusted
(tests/test_chat_capture.py). The caller supplies the deny-check function so
this module stays free of database and deployment concerns.
"""
import re

# Health, home, family logistics, shopping, personal admin. Deliberately broad:
# a false skip costs one un-saved chat, a false keep puts private life in a
# searchable index.
_PERSONAL_PATTERNS = [
    r"\b(?:symptom|swollen|rash|fever|doctor|dentist|prescription|pharmacy|"
    r"diagnos|urgent care|pediatric|medication|dose|allerg)\w*",
    r"\b(?:mattress|bed|rug|sofa|couch|curtain|dishwasher|vacuum|"
    r"furniture|ikea|paint colou?r|renovat)\w*",
    # household hardware and DIY, which reads technical but is not the work
    r"\b(?:gas spring|drawer slide|bed base|lift[- ]?up bed)\w*",
    r"\b(?:babysitter|daycare|preschool|birthday party|playdate|stroller|"
    r"car seat|potty)\w*",
    r"\b(?:movie tickets?|showtimes?|restaurant reservation|haircut|"
    r"grocer|dry clean)\w*",
    r"\b(?:flight change|passport renew|visa appointment|hotel booking|"
    r"packing list|what to pack)\b",
]

KEEP = "keep"
SKIP = "skip"


def personal_hit(text):
    """The personal-life pattern this text matches, or None. Pure."""
    low = (text or "").lower()
    for pat in _PERSONAL_PATTERNS:
        m = re.search(pat, low)
        if m:
            return m.group(0)
    return None


def should_capture(title, body, deny_check=None, work_terms=(), apply_deny=True):
    """(verdict, reason) — 'keep' or 'skip', with a human-readable why.

    deny_check: callable(text) -> matched pattern or None (the deployment's
    private-by-policy check; pass the memory layer's own so there is one list,
    not two). work_terms: caller-supplied vocabulary that rescues a chat whose
    personal wording is incidental to real work (a trip that is also a shoot).

    apply_deny: run the policy check or not. This exists because of WHERE the
    rule bites. A long working session mentions a dollar sign or the word
    "deliverables" somewhere in an hour of conversation, and the raw transcript
    is never what gets stored — so screening the raw text throws away the whole
    working record over one line. Screen the raw pass with apply_deny=False
    (personal-life only, cheap, before spending a distillation), and screen the
    DISTILLED note with apply_deny=True, because that is the copy that would
    actually land in the brain.
    """
    blob = f"{title}\n{body}"
    if not (title or "").strip() and not (body or "").strip():
        return SKIP, "empty"
    if deny_check and apply_deny:
        hit = deny_check(blob)
        if hit:
            return SKIP, f"private by policy (deny-list: {hit})"
    p = personal_hit(blob)
    if p:
        low = blob.lower()
        rescued = [w for w in work_terms if w.lower() in low]
        if rescued:
            return KEEP, f"personal wording ({p}) but work context: {rescued[0]}"
        return SKIP, f"personal life ({p})"
    return KEEP, "working record"
