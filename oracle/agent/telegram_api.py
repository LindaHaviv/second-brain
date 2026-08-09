"""Minimal Telegram Bot API helper — push a message out, and (for a DEDICATED bot) read
messages in. Runs headlessly from a launchd job, where the interactive Telegram tool isn't
available.

PUSH (send): when a DEDICATED brain bot is configured (TELEGRAM_DUMP_BOT_TOKEN), pushes
prefer it — so the chat that accepts your brain-dumps is the same chat that sends your
briefs and digests: one two-way thread. Otherwise zero-setup fallback: reuse the Claude
Code channel bot on this machine (`~/.claude/channels/telegram/{.env,access.json}`).
Override via env (keychain-aware): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. (A private
chat's id is the user's id, so the same TELEGRAM_CHAT_ID works for every bot — the user
just has to /start the new bot once before it may message them.)

DRAIN (read via getUpdates): use the SAME dedicated bot token — never the channel bot.
getUpdates consumes each update for exactly one reader, so a drain on the channel bot would
steal messages from your live Claude sessions (and vice versa). A dedicated brain bot has
no such conflict. Pass its token explicitly to get_updates()/send_message(token=...).

Absent everywhere -> `configured()` is False and callers skip gracefully.
"""
import json
import pathlib
import urllib.parse
import urllib.request

from keychain_secrets import getenv

_CHANNEL = pathlib.Path.home() / ".claude" / "channels" / "telegram"


def _token():
    # Pushes must come from a bot the chat has actually STARTED, and the default chat id
    # comes from the channel plugin's pairing (access.json) — so the CHANNEL bot's token
    # is the one that matches it. The dedicated dump bot is last resort only: a chat that
    # never pressed Start on it gets "400 chat not found" (which silently broke every
    # scheduled push once the dump token appeared). Drain flows are unaffected — they
    # pass the dump token explicitly.
    t = getenv("TELEGRAM_BOT_TOKEN")
    if t:
        return t
    envp = _CHANNEL / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                if tok:
                    return tok
    return getenv("TELEGRAM_DUMP_BOT_TOKEN")


def _chat_id():
    c = getenv("TELEGRAM_CHAT_ID")
    if c:
        return str(c)
    aj = _CHANNEL / "access.json"
    if aj.exists():
        try:
            af = json.load(open(aj)).get("allowFrom") or []
            if af:
                return str(af[0])
        except Exception:
            pass
    return None


def configured() -> bool:
    return bool(_token() and _chat_id())


def send_message(text: str, token: str | None = None, chat_id: str | None = None,
                 silent: bool = False) -> dict:
    """Send a message. Default (no token) uses the push/channel bot + allow-listed chat;
    pass token+chat_id explicitly to reply from a dedicated bot. `silent=True` delivers
    without a notification ping — for messages that should be waiting, not waking."""
    tok = token or _token()
    cid = chat_id or _chat_id()
    if not (tok and cid):
        raise RuntimeError("telegram not configured (no token / chat id)")
    payload = {"chat_id": cid, "text": text, "disable_web_page_preview": "true"}
    if silent:
        payload["disable_notification"] = "true"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if not out.get("ok"):
        raise RuntimeError(f"telegram sendMessage failed: {out.get('description', '?')}")
    return out


def send_document(path, caption="", token=None, chat_id=None):
    """Send a file to the chat — work product travels to the phone, not just news
    of it. Multipart via stdlib; Telegram caps bot uploads at 50MB."""
    import pathlib
    tok = token or _token()
    cid = chat_id or _chat_id()
    if not (tok and cid):
        raise RuntimeError("telegram not configured (no token / chat id)")
    p = pathlib.Path(path)
    data = p.read_bytes()
    if len(data) > 50 * 1024 * 1024:
        raise ValueError(f"{p.name} exceeds Telegram's 50MB bot limit")
    boundary = "----braindoc51c2ae"
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n"
        f"{cid}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n"
        f"{caption[:1000]}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
        f"filename=\"{p.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode(),
        data, f"\r\n--{boundary}--\r\n".encode()])
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendDocument", data=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    if not out.get("ok"):
        raise RuntimeError(f"telegram sendDocument failed: {out.get('description', '?')}")
    return out


def get_updates(token: str, offset: int | None = None) -> list:
    """Poll a DEDICATED bot for new messages (getUpdates, non-blocking). `offset` is the
    next update_id to fetch (last seen + 1) — Telegram then also acks everything before it."""
    q = {"timeout": 0}
    if offset is not None:
        q["offset"] = offset
    url = f"https://api.telegram.org/bot{token}/getUpdates?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=35) as r:
        out = json.loads(r.read())
    if not out.get("ok"):
        raise RuntimeError(f"telegram getUpdates failed: {out.get('description', '?')}")
    return out.get("result", [])


def parse_updates(updates: list, allow_chat_id: str | None = None) -> list:
    """Pure: raw getUpdates result -> [{update_id, ts, text, chat_id}] for real text messages,
    chronological. When `allow_chat_id` is set, keep ONLY that chat (so a stray sender to the
    bot can't inject into the backlog — the same allowlist idea as the channel plugin)."""
    out = []
    for u in updates or []:
        if not isinstance(u, dict):
            continue
        msg = u.get("message") or u.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        uid = u.get("update_id")
        chat = str((msg.get("chat") or {}).get("id", ""))
        if not text or uid is None or not chat:
            continue
        if allow_chat_id and chat != str(allow_chat_id):
            continue
        out.append({"update_id": uid, "ts": msg.get("date"), "text": text, "chat_id": chat})
    out.sort(key=lambda x: x["update_id"])
    return out
