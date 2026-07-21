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
