"""Pipeline watchdog — makes the alarm PUSH, not pull. Silence means healthy.

  ./.venv/bin/python scripts/watchdog.py           # check; message only if something is wrong

The status panel only helps if you look at it, so this scheduled check reads the same
health signals the panel does and sends ONE message to your messaging channel only when
the pipeline needs a human:

  - DOWN / no heartbeat  — no sync run inside the expected window (machine off/asleep,
    job unloaded, or the sync itself is crashing before its heartbeat).
  - REPEATED FAILURES    — a step that failed every one of the last few runs (the same
    consecutive-fail streak the hygiene report headlines). One-off failures and
    deliberate skips do NOT alert: a watchdog that messages daily gets muted, then
    ignored — quiet stays the design (see docs/LOOP_ENGINEERING.md).

Delivery reuses the existing Telegram helper (oracle/agent/telegram_api.py): zero-setup
if a Claude Code Telegram channel is configured on this machine, overridable via
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (keychain-aware). Not configured -> the verdict
still prints to the log and the exit code still escalates; nothing breaks.

KNOWN BLIND SPOT (documented, accepted): this runs on the same machine as the sync, so
a sleeping machine alerts only after it wakes ("this broke while you were away"). The
hosted status panel remains the real-time view from other devices.

Exit codes: 0 healthy or alert sent, 1 degraded-and-undeliverable (so launchd logs show it).
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
sys.path.insert(0, str(HERE))

from dotenv import load_dotenv
load_dotenv(ROOT / "oracle" / ".env")

import json  # noqa: E402

import db          # noqa: E402
import health      # noqa: E402


def compose_alert(verdict, streaks):
    """Pure + unit-tested: the message to push, or None when nothing warrants one.
    Alert-worthy = pipeline down / never ran, or a consecutive-fail streak. A merely
    'degraded' run (one-off fail, deliberate skip) stays silent — the hygiene report
    and status panel carry those."""
    lines = []
    if verdict["state"] in ("down", "no-heartbeat"):
        since = (f"last sync run {verdict['hours_since']}h ago"
                 if verdict["hours_since"] is not None else "no sync run recorded yet")
        lines.append(f"second brain: pipeline {verdict['state'].upper()} — {since}")
    for s in streaks:
        lines.append(f"second brain: {s['step']} step has failed "
                     f"{s['consecutive_fails']}+ runs in a row (last: {s['last_run']})")
    return "\n".join(lines) or None


def webhook_alert(expected_url, info, now=None):
    """Pure + unit-tested: is the chat webhook (a phone front door pointed at the
    hosted server) still receiving? Alert when the webhook is unset/wrong — a
    polling listener silently steals it back — or when Telegram reports delivery
    errors in the last 24h. `info` = getWebhookInfo()['result']. Silence must be
    loud: a deaf front door looks identical to a quiet day without this check."""
    if not expected_url:
        return None
    url = (info or {}).get("url") or ""
    if url != expected_url:
        what = "UNSET" if not url else f"WRONG ({url})"
        return (f"second brain: chat webhook {what} — the phone front door is deaf. "
                f"Expected {expected_url}; a polling listener may have stolen it "
                f"(re-run setWebhook).")
    led = (info or {}).get("last_error_date")
    if led and now and 0 <= (now - led) < 24 * 3600:
        msg = ((info or {}).get("last_error_message") or "")[:120]
        return (f"second brain: chat webhook deliveries FAILING — last error "
                f"{int((now - led) // 3600)}h ago: {msg}")
    return None


def _check_webhook():
    """Fetch getWebhookInfo when TELEGRAM_WEBHOOK_URL is configured; a fetch
    failure is itself alert-worthy (can't-check must not read as healthy).
    SELF-REPAIR: when the webhook is unset/wrong and TELEGRAM_WEBHOOK_SECRET is
    available, re-run setWebhook instead of only complaining — a stray polling
    listener deletes the webhook as a side effect of starting, and the fix is
    deterministic. The alert still fires, downgraded to a repair notice."""
    expected = os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip()
    if not expected:
        return None
    import telegram_api
    import time
    import urllib.parse
    import urllib.request
    tok = telegram_api._token()
    if not tok:
        return "second brain: TELEGRAM_WEBHOOK_URL set but no bot token — can't check the front door"
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{tok}/getWebhookInfo", timeout=30) as r:
            info = json.loads(r.read()).get("result", {})
    except Exception as e:
        return f"second brain: webhook check FAILED ({type(e).__name__}) — front door state unknown"
    alert = webhook_alert(expected, info, now=time.time())
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if alert and ("UNSET" in alert or "WRONG" in alert) and secret:
        try:
            data = urllib.parse.urlencode({
                "url": expected, "secret_token": secret,
                "allowed_updates": '["message"]'}).encode()
            with urllib.request.urlopen(
                    f"https://api.telegram.org/bot{tok}/setWebhook", data=data,
                    timeout=30) as r:
                ok = json.loads(r.read()).get("ok")
            if ok:
                return ("second brain: chat webhook was UNSET (a polling listener "
                        "stole it) — REPAIRED, front door is back. Root fix: stop "
                        "whatever polls this bot.")
        except Exception as e:
            return alert + f" (auto-repair failed: {type(e).__name__})"
    return alert


def main():
    conn = db.connect()
    try:
        verdict = health.verdict(health.last_heartbeat(conn))
    finally:
        conn.close()
    try:
        from memory_review import failing_streaks
        status = ROOT / "exports" / "sync_status.json"
        history = json.loads(status.read_text()) if status.exists() else []
        streaks = failing_streaks(history)
    except Exception:
        streaks = []   # a malformed ledger must not silence the down/no-heartbeat check

    alert = compose_alert(verdict, streaks)
    wh = _check_webhook()
    if wh:
        alert = (alert + "\n" + wh) if alert else wh
    if not alert:
        print(f"watchdog: healthy (state={verdict['state']}, "
              f"last run {verdict['hours_since']}h ago) — silence means healthy")
        return
    print("watchdog: ALERT\n" + alert)
    import telegram_api
    if not telegram_api.configured():
        print("watchdog: no messaging channel configured — alert is log-only "
              "(set TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID or pair the Claude Code channel)")
        sys.exit(1)
    telegram_api.send_message(alert)
    print("watchdog: alert delivered")


if __name__ == "__main__":
    main()
