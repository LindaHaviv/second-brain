"""Chat-export parsing — the pure logic of reading a WhatsApp chat export.

WhatsApp has no read API for personal chats, and scraping is off the table
(rule 7) — but the app ships a user-requested export (chat → Export Chat),
which is exactly the sanctioned shape: the user hands over a .txt, a loader
parses it. This module owns only the parsing and the last-state question, so
both can be unit-tested; the caller does the file I/O and the verdicts
(followup_core takes it from here).

Handles both export dialects:

  iOS      [8/5/26, 2:14:33 PM] Sender Name: message text
  Android  8/5/26, 2:14 PM - Sender Name: message text   (also 24h times)

plus multiline messages (continuation lines attach to the previous message)
and system lines (encryption notices, "image omitted" — no "Sender:" part —
which are dropped). Dates are tried US-first (M/D/Y), then D/M/Y; the first
format that parses a file's first message wins for the whole file, so one
ambiguous line can't flip the calendar mid-thread.
"""
import datetime
import re

# [8/5/26, 2:14:33 PM] Sender: text        (iOS; ‎ marks stripped first)
_IOS = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?P<sender>[^:]+):\s?(?P<text>.*)$")
# 8/5/26, 2:14 PM - Sender: text           (Android)
_ANDROID = re.compile(
    r"^(?P<ts>\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)"
    r"\s+-\s+(?P<sender>[^:]+):\s?(?P<text>.*)$")

_FORMATS = (
    "%m/%d/%y, %I:%M:%S %p", "%m/%d/%y, %I:%M %p",
    "%m/%d/%Y, %I:%M:%S %p", "%m/%d/%Y, %I:%M %p",
    "%m/%d/%y, %H:%M:%S", "%m/%d/%y, %H:%M",
    "%m/%d/%Y, %H:%M",
    "%d/%m/%y, %I:%M:%S %p", "%d/%m/%y, %I:%M %p",
    "%d/%m/%Y, %I:%M:%S %p", "%d/%m/%Y, %I:%M %p",
    "%d/%m/%y, %H:%M:%S", "%d/%m/%y, %H:%M",
    "%d/%m/%Y, %H:%M",
)


def _try_ts(ts, formats=_FORMATS):
    ts = re.sub(r"\s+", " ", ts.replace(" ", " ").replace(" ", " ")).strip()
    for f in formats:
        try:
            return datetime.datetime.strptime(ts, f), f
        except ValueError:
            continue
    return None, None


def parse(text):
    """Export text -> [{"at": datetime, "sender": str, "text": str}], oldest first.
    Continuation lines fold into the previous message; lines with no sender
    (system notices) are dropped."""
    msgs, fmt = [], None
    for raw in (text or "").splitlines():
        line = raw.replace("‎", "").rstrip()
        m = _IOS.match(line) or _ANDROID.match(line)
        if m:
            at, used = _try_ts(m.group("ts"), (fmt,) if fmt else _FORMATS)
            if at is None and fmt:            # locked format failed — try the rest
                at, used = _try_ts(m.group("ts"))
            if at is not None:
                fmt = fmt or used             # first successful format wins the file
                msgs.append({"at": at, "sender": m.group("sender").strip(),
                             "text": m.group("text")})
                continue
        if msgs and line and not (_IOS.match(line) or _ANDROID.match(line)):
            msgs[-1]["text"] += "\n" + line   # multiline continuation
    return msgs


def last_state(msgs, owner_names):
    """The one question the chaser asks of a chat: who spoke last, and when.
    Returns {"last_at", "last_sender", "owner_last", "froms"} or None if empty.
    `froms` is the sender-per-message list, ready for followup_core verdicts."""
    if not msgs:
        return None
    owners = [o.lower() for o in owner_names if o]

    def is_owner(sender):
        s = sender.lower()
        return any(o in s for o in owners)

    return {"last_at": msgs[-1]["at"],
            "last_sender": msgs[-1]["sender"],
            "owner_last": is_owner(msgs[-1]["sender"]),
            "froms": [m["sender"] for m in msgs]}
