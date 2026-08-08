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

The mirror question — is the OWNER leaving someone hanging? — gets its own
verdict (`owed_reply`), because a lane of people the owner works with (editors,
collaborators, guests) needs BOTH directions: who owes the owner a reply, and
whom the owner owes one. Same inputs, same first-match-wins style:

  empty            — no messages: nothing to decide.
  self-only        — no counterparty; nothing can be owed.
  waiting-on-them  — the owner spoke last: the ball is in the other court
                     (thread_verdict is the tool for that direction).
  fresh            — they spoke last, but within the grace window.
  owes             — they spoke last and it has sat past the grace window:
                     the owner owes a reply.

Shared by: private follow-up agents (the personal layer adds queries, lane
configs, nudge copy, and reporting); tests/test_followup.py pins every rule.
"""
import datetime

QUIET_DAYS = 4   # default: owner's message unanswered this long -> chase
OWE_DAYS = 3     # default: their message unanswered this long -> the owner owes


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


def owed_reply(froms, owner, last_at, now, owe_days=OWE_DAYS):
    """The mirror of thread_verdict: does the OWNER owe this thread a reply?
    froms: From-header per message, oldest first. Returns one of
    'empty' | 'self-only' | 'waiting-on-them' | 'fresh' | 'owes'."""
    if not froms:
        return "empty"
    if all(sender_is(owner, f) for f in froms):
        return "self-only"
    if sender_is(owner, froms[-1]):
        return "waiting-on-them"
    if (now - last_at) < datetime.timedelta(days=owe_days):
        return "fresh"
    return "owes"
