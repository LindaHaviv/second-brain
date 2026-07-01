"""Backfill Instagram transcripts from a data export (.srt auto-caption files).

The export's video/image files aren't needed (the brain searches text) — this pulls the SPOKEN
content (auto-caption transcripts) of your reels/posts into the brain as instagram items
(content scope). Ongoing captions + performance come from the API loader (scripts/instagram.py).

  ../.venv/bin/python scripts/instagram_export.py /path/to/extracted-export
Idempotent per item (dedups on the reel URL). Non-English auto-translations are skipped.
"""
import datetime
import glob
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
import db  # noqa: E402  (wallet-aware, loads oracle/.env)


def parse_srt(text):
    """Strip sequence numbers + timestamps, keep the spoken lines."""
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.isdigit() or "-->" in ln:
            continue
        out.append(ln)
    return " ".join(out)


def mostly_english(t):
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    return sum(ord(c) < 128 for c in letters) / len(letters) > 0.7


def date_from_path(p):
    m = re.search(r"/(20\d{2})(\d{2})/", p)   # media/reels/YYYYMM/
    return datetime.datetime(int(m.group(1)), int(m.group(2)), 1) if m else None


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: instagram_export.py /path/to/extracted-export")
    files = glob.glob(os.path.join(sys.argv[1], "**", "*.srt"), recursive=True)
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("alter session disable parallel dml")
    cur.execute("merge into platforms p using (select 'instagram' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('instagram','Instagram')")
    n = skip = 0
    for f in files:
        txt = parse_srt(open(f, encoding="utf-8", errors="ignore").read()).strip()
        if len(txt) < 40 or not mostly_english(txt):
            skip += 1
            continue
        mid = os.path.splitext(os.path.basename(f))[0]
        kind = "reel" if "/reels/" in f.replace("\\", "/") else "post"
        url = f"https://www.instagram.com/reel/{mid}/"
        title = txt[:120]
        cur.execute("delete from posts where url = :u", u=url)   # idempotent per item
        cur.execute(
            """insert into posts (platform_id, kind, title, caption, url, published_at,
                   visibility, content_embedding)
               values ('instagram', :k, :t, :c, :u, :p, 'content',
                   vector_embedding(MINILM using :e as data))""",
            k=kind, t=title, c=txt[:4000], u=url, p=date_from_path(f),
            e=(title + ". " + txt)[:3000])
        n += 1
    conn.commit()
    print(f"ingested {n} Instagram transcripts ({skip} skipped: too short / non-English)")
    conn.close()


if __name__ == "__main__":
    main()
