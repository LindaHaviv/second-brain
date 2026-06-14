"""Collect step (YouTube): yt-dlp metadata -> canonical Markdown + Oracle.

Pipeline:
  1. yt-dlp --dump-json  (already run -> exports/youtube/videos.jsonl)
  2. this script: each video -> sources/youtube/<id>.md  (canonical layer)
                              -> a row in Oracle `posts` (Duality model) with an
                                 in-DB embedding for semantic search.

The SAME shape works for every platform — they all land in `posts` — so "collect"
scales from YouTube to Instagram to podcast transcripts without changing the database.

Run from repo root:  ./.venv/bin/python scripts/youtube.py
"""
import datetime
import json
import os
import pathlib

import oracledb
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "oracle" / ".env")
oracledb.defaults.fetch_lobs = False

SRC = ROOT / "sources" / "youtube"
EXPORT = ROOT / "exports" / "youtube" / "videos.jsonl"


def connect():
    return oracledb.connect(
        user=os.environ.get("DB_USER", "CCC"),
        password=os.environ.get("APP_PWD", "CccPwd_2026"),
        dsn=os.environ.get("DB_DSN", "localhost:1521/FREEPDB1"),
    )


def yyyymmdd(s):
    return datetime.datetime.strptime(s, "%Y%m%d") if s else None


def frontmatter(v):
    fm = {
        "id": f"yt_{v['id']}", "platform": "youtube", "type": "video",
        "url": v.get("webpage_url"), "published": v.get("upload_date"),
        "title": v.get("title"), "views": v.get("view_count"),
        "duration_s": v.get("duration"),
    }
    lines = ["---"]
    for k, val in fm.items():
        lines.append(f"{k}: {json.dumps(val) if isinstance(val, str) else val}")
    lines.append("---\n")
    return "\n".join(lines)


def main():
    SRC.mkdir(parents=True, exist_ok=True)
    videos = [json.loads(l) for l in open(EXPORT) if l.strip()]
    conn = connect()
    cur = conn.cursor()

    # platform must exist (FK)
    cur.execute("merge into platforms p using (select 'youtube' id from dual) s "
                "on (p.platform_id=s.id) when not matched then "
                "insert (platform_id, display_name) values ('youtube','YouTube')")
    # clean reload of YouTube content
    cur.execute("delete from posts where platform_id='youtube'")

    for v in videos:
        title = v.get("title") or ""
        desc = v.get("description") or ""
        # 1) canonical Markdown
        (SRC / f"{v['id']}.md").write_text(frontmatter(v) + f"# {title}\n\n{desc}\n")
        # 2) Oracle row, with the embedding generated INSIDE the database
        emb_text = (title + ". " + desc)[:3000]
        cur.execute(
            """
            insert into posts (platform_id, kind, title, caption, url, published_at,
                               views, content_embedding)
            values ('youtube','video', :title, :caption, :url, :pub, :views,
                    vector_embedding(MINILM using :emb as data))
            """,
            title=title, caption=desc, url=v.get("webpage_url"),
            pub=yyyymmdd(v.get("upload_date")), views=v.get("view_count") or 0,
            emb=emb_text,
        )
    conn.commit()
    cur.execute("select count(*) from posts where platform_id='youtube'")
    print(f"loaded {cur.fetchone()[0]} YouTube videos -> sources/youtube/ + Oracle posts")
    conn.close()


if __name__ == "__main__":
    main()
