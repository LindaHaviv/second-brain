"""Wipe the sample content (and everything derived from it) for a clean start.

Run this ONCE before loading your own sources, so the sample-channel videos
don't linger in your real brain:

  ./.venv/bin/python scripts/reset_sample.py

Deletes: posts, chunks, media, analytics, the compiled wiki, and the agent
memory built while you played with the sample. Keeps: the schema, the loaded
embedding model, platform lookups, and procedural (tool) memory.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "oracle" / "agent"))
import db  # noqa: E402

# children first, parents last
TABLES = [
    "page_sources",
    "page_links",
    "wiki_pages",
    "wiki_meta",
    "content_chunks",
    "analytics",
    "media",
    "posts",
    "agent_memory",
    "semantic_memory",
    "conversations",
]


def main():
    conn = db.connect()
    cur = conn.cursor()
    for t in TABLES:
        try:
            cur.execute(f"DELETE FROM {t}")
            print(f"  {t}: {cur.rowcount} rows deleted")
        except Exception as e:  # table may not exist in older schemas
            print(f"  {t}: skipped ({e})")
    conn.commit()
    conn.close()
    print("Done. The brain is empty and ready for your own sources.")


if __name__ == "__main__":
    main()
