"""Scheduled YouTube harvest: yt-dlp recent-uploads dump -> exports/, then ingest.

The YouTube pipeline is two-stage (harvest -> `scripts/youtube.py` ingest). This wrapper
makes stage 1 schedulable the same way the LinkedIn step is:
  - Cadence gate: the daily sync calls this, but it no-ops unless >= 14 days since the
    last successful harvest (yt-dlp hits YouTube's public pages — keep it infrequent).
  - Recent-only: pulls metadata for the newest uploads per tab, not the full channel;
    `youtube.py` merges every *.jsonl in exports/youtube/ and dedups by id, so the old
    full dumps keep the back catalog.
  - Fail-closed: zero harvested items exits non-zero, which lands in the sync
    heartbeat -> health panel -> freshness report.

Set in oracle/.env:  YT_CHANNEL_URL=https://www.youtube.com/@yourhandle

Run from repo root:  ./.venv/bin/python scripts/youtube_harvest.py
"""
import datetime
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "oracle" / ".env")

CADENCE_DAYS = 14
MARKER = ROOT / "exports" / ".youtube_harvest_last"
EXPORT_DIR = ROOT / "exports" / "youtube"
YTDLP = pathlib.Path(sys.executable).parent / "yt-dlp"
TABS = [("shorts", 15), ("videos", 10)]   # (channel tab, newest N to pull)


def ran_recently():
    try:
        last = datetime.date.fromisoformat(MARKER.read_text().strip())
        return (datetime.date.today() - last).days < CADENCE_DAYS
    except Exception:
        return False


def main():
    channel = os.environ.get("YT_CHANNEL_URL", "").rstrip("/")
    if not channel:
        sys.exit("youtube_harvest: set YT_CHANNEL_URL (https://www.youtube.com/@handle) "
                 "in oracle/.env")
    if ran_recently():
        print(f"youtube harvest ran <{CADENCE_DAYS}d ago — nothing to do")
        return
    if not YTDLP.exists():
        sys.exit("youtube_harvest: yt-dlp not found in the venv — "
                 "./.venv/bin/pip install yt-dlp")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    harvested = 0
    for tab, count in TABS:
        out = EXPORT_DIR / f"{tab}-recent.jsonl"
        r = subprocess.run(
            [str(YTDLP), "--skip-download", "--dump-json",
             "--playlist-items", f"1-{count}", f"{channel}/{tab}"],
            capture_output=True, text=True, timeout=600)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        if r.returncode != 0 and not lines:
            print(f"  [warn] {tab} tab failed: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'no output'}", flush=True)
            continue
        out.write_text("\n".join(lines) + "\n")
        print(f"  {tab}: {len(lines)} items -> {out.relative_to(ROOT)}", flush=True)
        harvested += len(lines)
    if not harvested:
        sys.exit("youtube_harvest: 0 items from every tab — YouTube layout change or "
                 "block? Run manually to inspect.")

    ingest = subprocess.run([sys.executable, str(ROOT / "scripts" / "youtube.py")])
    if ingest.returncode != 0:
        sys.exit("youtube_harvest: harvest ok but ingest failed — see above")
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(datetime.date.today().isoformat())


if __name__ == "__main__":
    main()
