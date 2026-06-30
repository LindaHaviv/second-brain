"""Copy the whole brain from the local Oracle container to the cloud Autonomous DB.

Faithful, fast migration: copies every table (content, chunks, all memory types, the compiled
wiki) INCLUDING precomputed embeddings — so no re-ingest, no embedding recomputation, no
re-hitting source APIs. db.connect() is the CLOUD target (oracle/.env); local is explicit.

  python scripts/copy_local_to_cloud.py

Idempotent: clears the target tables first. Run after apply_schema.py + load_model_cloud.py.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
import db          # noqa: E402  (cloud target, from oracle/.env)
import oracledb    # noqa: E402

# FK-safe order: parents before children.
TABLES = ["platforms", "brands", "deals", "posts", "media", "content_chunks",
          "agent_memory", "semantic_memory", "conversations", "procedural_memory",
          "wiki_pages", "page_links", "page_sources", "wiki_meta"]
BATCH = 500


def cols_of(cur, table):
    cur.execute("SELECT column_name FROM user_tab_columns WHERE table_name = :t "
                "ORDER BY column_id", t=table.upper())
    return [r[0] for r in cur.fetchall()]


def vector_cols(cur, table):
    cur.execute("SELECT column_name FROM user_tab_columns WHERE table_name = :t "
                "AND data_type = 'VECTOR'", t=table.upper())
    return {r[0] for r in cur.fetchall()}


def main():
    local = oracledb.connect(user="CCC", password="CHANGE_ME_AppPwd1",
                             dsn="localhost:1521/FREEPDB1")
    cloud = db.connect()
    lc, cc = local.cursor(), cloud.cursor()

    print("clearing target tables...")
    for t in reversed(TABLES):
        cc.execute(f"DELETE FROM {t}")
    cloud.commit()

    for t in TABLES:
        cols = cols_of(lc, t)
        vcols = vector_cols(lc, t)
        col_list = ", ".join(cols)
        binds = ", ".join(f":{i+1}" for i in range(len(cols)))
        insert = f"INSERT INTO {t} ({col_list}) VALUES ({binds})"
        sizes = [oracledb.DB_TYPE_VECTOR if c in vcols else None for c in cols]

        lc.execute(f"SELECT {col_list} FROM {t}")
        total = 0
        while True:
            rows = lc.fetchmany(BATCH)
            if not rows:
                break
            if any(sizes):
                cc.setinputsizes(*sizes)
            cc.executemany(insert, rows)
            total += len(rows)
        cloud.commit()
        print(f"  {t}: {total} rows")

    local.close()
    cloud.close()
    print("done — brain copied to the cloud.")


if __name__ == "__main__":
    main()
