"""Backfill Instagram from a data export — captions + dates + spoken-word transcripts.

The export's video/image files aren't needed (the brain searches text). This reads reels.json +
posts.json for your CAPTIONS and dates, and enriches each reel with its auto-caption TRANSCRIPT
(the matching .srt) — so your hooks AND what you said land in the brain (content scope). Ongoing
performance metrics come from the API loader (scripts/instagram.py).

  ../.venv/bin/python scripts/instagram_export.py /path/to/extracted-export-root
Idempotent per item (dedups on reel URL). Non-English auto-translations are dropped from the
transcript but the caption is still kept.
"""
import datetime
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
import db  # noqa: E402


def fix(s):
    """Instagram exports double-encode UTF-8 as latin-1 (emoji show as 'ð¤¯'). Undo it."""
    if not s:
        return ""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def parse_srt(path):
    if not path or not os.path.exists(path):
        return ""
    out = []
    for ln in open(path, encoding="utf-8", errors="ignore").read().splitlines():
        ln = ln.strip()
        if ln and not ln.isdigit() and "-->" not in ln:
            out.append(ln)
    return " ".join(out)


def mostly_english(t):
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and sum(ord(c) < 128 for c in letters) / len(letters) > 0.7


def _dig(d, *path):
    for k in path:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    return d


def reels(root):
    f = root / "your_instagram_activity" / "media" / "reels.json"
    if not f.exists():
        return
    for it in json.load(open(f)).get("ig_reels_media", []):
        m = (it.get("media") or [{}])[0]
        yield {
            "caption": fix(m.get("title") or it.get("title") or ""),
            "ts": m.get("creation_timestamp") or it.get("creation_timestamp"),
            "sub": _dig(m, "media_metadata", "video_metadata", "subtitles", "uri"),
            "uri": m.get("uri", ""), "kind": "reel",
        }


def photo_posts(root):
    f = root / "your_instagram_activity" / "media" / "posts.json"
    if not f.exists():
        return
    for it in json.load(open(f)):
        media = it.get("media") or []
        if not media:  # caption-only posts nest media under label_values
            for lv in it.get("label_values", []):
                if lv.get("media"):
                    media = lv["media"]
                    break
        m = (media or [{}])[0]
        yield {
            "caption": fix(m.get("title") or ""),
            "ts": it.get("timestamp") or m.get("creation_timestamp"),
            "sub": None, "uri": m.get("uri", ""), "kind": "post",
        }


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: instagram_export.py /path/to/extracted-export-root")
    root = pathlib.Path(sys.argv[1])
    conn = db.connect()
    cur = conn.cursor()
    cur.execute("alter session disable parallel dml")
    cur.execute("merge into platforms p using (select 'instagram' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('instagram','Instagram')")
    n = skip = 0
    for it in list(reels(root)) + list(photo_posts(root)):
        cap = (it["caption"] or "").strip()
        transcript = parse_srt(str(root / it["sub"])) if it["sub"] else ""
        if transcript and not mostly_english(transcript):
            transcript = ""
        body = cap + (("\n\n[transcript] " + transcript) if transcript else "")
        if len(body.strip()) < 20:
            skip += 1
            continue
        mid = os.path.splitext(os.path.basename(it["uri"]))[0] or str(it["ts"])
        url = f"https://www.instagram.com/reel/{mid}/"
        title = (cap.split("\n", 1)[0] or transcript)[:150]
        pub = datetime.datetime.utcfromtimestamp(it["ts"]) if it["ts"] else None
        cur.execute("delete from posts where url = :u", u=url)
        cur.execute(
            """insert into posts (platform_id, kind, title, caption, url, published_at,
                   visibility, content_embedding)
               values ('instagram', :k, :t, :c, :u, :p, 'content',
                   vector_embedding(MINILM using :e as data))""",
            k=it["kind"], t=title, c=body[:4000], u=url, p=pub, e=(title + ". " + body)[:3000])
        n += 1
    conn.commit()
    total = cur.execute("select count(*) from posts where platform_id='instagram'").fetchone()[0]
    print(f"ingested {n} Instagram items ({skip} skipped as empty); total instagram now {total}")
    conn.close()


if __name__ == "__main__":
    main()
