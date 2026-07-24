"""Fly-side sentinel — the vendor-neutral backstop for pipeline alerts.

The local watchdog (scripts/watchdog.py) has a documented blind spot: it runs on the
same machine as the sync, so a sleeping Mac alerts only after it wakes. A cloud
scheduled agent covers that, but it belongs to an AI vendor. This module is the third
layer, on infrastructure the user already owns: the hosted MCP server runs 24/7 next to
the database that holds the sync heartbeat, so a daemon thread here can notice a dead
pipeline and push a Telegram message with no AI vendor in the loop at all.

Scope is deliberately narrower than the local watchdog: only DOWN / no-heartbeat alert
(the pipeline is properly dead). Degraded runs and failing streaks stay with the richer
layers; a backstop that comments on everything is noise, and silence means healthy.

Quiet is stateless: alerting decisions derive from hours_since alone, so no state
survives restarts and none is needed. First day past the window alerts, then every third
day while the outage persists (day 0, 3, 6, ...). With the default 24h check interval
each day is evaluated about once. Known wrinkle, accepted: a redeploy mid-outage restarts
the clock on the process (not the math), so an alert-day deploy can repeat that day's
message.

Config (all optional):
  SENTINEL=0                off switch (default on; sends only if Telegram is configured)
  SENTINEL_INTERVAL_HOURS   check cadence, default 24
  SYNC_EXPECTED_HOURS       same window source_status uses, default health.EXPECTED_HOURS
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID   delivery (Fly secrets); unset -> log-only

Requires min_machines_running >= 1 (private/server/fly.toml): a machine that scales to
zero takes its sentinel down with it.
"""
import math
import os
import time

import health


def should_alert(verdict, expected_hours=None):
    """Pure + unit-tested. verdict (from health.verdict) -> message string or None.
    ok/degraded -> None. down -> message on day 0 past the window, then every 3rd day.
    no-heartbeat -> always message (pre-first-sync or missing table: worth daily noise)."""
    state = verdict.get("state")
    if state not in ("down", "no-heartbeat"):
        return None
    expected = expected_hours if expected_hours is not None else float(
        os.environ.get("SYNC_EXPECTED_HOURS", health.EXPECTED_HOURS))
    hours = verdict.get("hours_since")
    if state == "down":
        days_overdue = math.floor(max(0.0, hours - expected) / 24)
        if days_overdue % 3 != 0:
            return None
        since = f"last successful run {hours:.0f}h ago (about {hours / 24:.1f} days)"
    else:
        since = "no sync run recorded in the database at all"
    return (f"🚨 second brain backstop (Fly): the daily sync looks dead: {since}. "
            "Search and the wiki still work; new content is not being ingested. "
            "Usual cause: the Mac that runs the sync is asleep or the job is unloaded. "
            "I re-check daily and re-alert every 3 days while this persists.")


def check_once():
    """One check: read the heartbeat, decide, deliver. Returns the message sent (or that
    would have been sent, when Telegram is unconfigured) for logging/tests; None if quiet."""
    import db
    conn = db.connect()
    try:
        verdict = health.verdict(health.last_heartbeat(conn))
    finally:
        conn.close()
    msg = should_alert(verdict)
    if not msg:
        print(f"[sentinel] healthy (state={verdict['state']}, "
              f"hours_since={verdict['hours_since']}) — silence means healthy", flush=True)
        return None
    import telegram_api
    if telegram_api.configured():
        telegram_api.send_message(msg)
        print("[sentinel] alert delivered", flush=True)
    else:
        print("[sentinel] ALERT (log-only, no Telegram configured): " + msg, flush=True)
    return msg


def run_forever():
    """Daemon-thread target, same shape as mcp_http's keep-warm loop: short startup
    delay (skip transient boot states during a deploy), then check every interval.
    Failures never kill the thread — a broken check must not take the server with it."""
    time.sleep(float(os.environ.get("SENTINEL_STARTUP_DELAY", "300")))
    interval = float(os.environ.get("SENTINEL_INTERVAL_HOURS", "24")) * 3600
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"[sentinel] check failed: {e}", flush=True)
        time.sleep(interval)
