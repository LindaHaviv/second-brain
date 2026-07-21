"""Follow-up verdicts — the pure logic of "is anyone leaving me hanging?"

Given the sender history of an email thread, decide whether it needs a follow-up
nudge from the owner. Deliberately tiny and dependency-free: the caller does the
I/O (Gmail, IMAP, anything that yields From-headers and a timestamp); this module
owns only the decision, so the decision can be unit-tested and never drifts.

The rules, in order (first match wins):

  empty      — no messages: nothing to decide.
  self-only  — every message is from the owner. There is no counterparty to chase:
               notes-to-self, drafts, the owner's own newsletter/digest mails. This
               rule matters more than it looks: content searches happily match the
               owner's OWN outbound mail that mentions a term (in the original
               deployment it removed 20 of 21 flags).
  answered   — the last message is NOT from the owner: the counterparty has spoken
               last, so the ball is in the owner's court. A different problem, not
               a chase.
  too-recent — the owner spoke last but inside the quiet window: give them time.
  chase      — the owner spoke last, to a real counterparty, and the thread has
               been quiet past the threshold. Nudge time.

Shared by: private follow-up agents (the personal layer adds queries, nudge
copy, and reporting); tests/test_followup.py pins every rule.
"""
import datetime

QUIET_DAYS = 4   # default: owner's message unanswered this long -> chase


def sender_is(owner, from_header):
    """Does a From header belong to the owner? Case-insensitive containment, so
    'Linda <linda@example.com>' matches owner 'linda@example.com'."""
    return bool(owner) and owner.lower() in (from_header or "").lower()


def thread_verdict(froms, owner, last_at, now, quiet_days=QUIET_DAYS):
    """froms: From-header per message, oldest first. Returns one of
    'empty' | 'self-only' | 'answered' | 'too-recent' | 'chase'."""
    if not froms:
        return "empty"
    if all(sender_is(owner, f) for f in froms):
        return "self-only"
    if not sender_is(owner, froms[-1]):
        return "answered"
    if (now - last_at) < datetime.timedelta(days=quiet_days):
        return "too-recent"
    return "chase"
