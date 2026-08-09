"""Webhook gating — the pure logic of "should this chat message reach the agent?"

A phone-chat front door (Telegram webhook or similar) is an unauthenticated
HTTP surface on a public host, so every request passes three gates before any
model or tool runs:

  forbidden — the shared webhook secret is missing or wrong: not Telegram (or
              a replay from someone who found the URL). The caller should 401.
  ignore    — authentic but not actionable: a non-message update (callback,
              edit, join event), a message with no text, or a chat that is not
              on the allowlist. The caller should 200 quietly — Telegram must
              not retry, and strangers must learn nothing.
  ok        — a text message from an allowed chat. Parsed fields returned.

Deliberately tiny and dependency-free: the caller does the HTTP and the reply;
this module owns only the decision, so it can be unit-tested and never drifts.
tests/test_chat_gate.py pins every gate.
"""


def gate(secret_header, expected_secret, update, allowed_chat_ids):
    """secret_header: X-Telegram-Bot-Api-Secret-Token value (or None).
    update: the parsed webhook JSON. allowed_chat_ids: iterable of str.
    Returns (verdict, info) — info populated only when verdict == 'ok':
    {chat_id, text, message_id, update_id, sender}."""
    if not expected_secret or secret_header != expected_secret:
        return "forbidden", None
    if not isinstance(update, dict):
        return "ignore", None
    msg = update.get("message")
    if not isinstance(msg, dict):
        return "ignore", None          # edits, callbacks, member events, channels
    text = msg.get("text")
    if not isinstance(text, str) or not text.strip():
        return "ignore", None          # media-only, stickers, joins
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if chat_id not in {str(c) for c in allowed_chat_ids if str(c).strip()}:
        return "ignore", None          # silent drop: strangers learn nothing
    sender = (msg.get("from") or {}).get("first_name", "") or "user"
    return "ok", {
        "chat_id": chat_id,
        "text": text.strip(),
        "message_id": msg.get("message_id"),
        "update_id": update.get("update_id"),
        "sender": sender,
    }
