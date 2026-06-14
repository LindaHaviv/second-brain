"""Oracle 26ai connection helper (thin mode — no Oracle client install needed)."""
import os
import oracledb
from dotenv import load_dotenv

load_dotenv()

# CLOB columns (action, detail) come back as str instead of LOB objects.
oracledb.defaults.fetch_lobs = False


def connect():
    return oracledb.connect(
        user=os.environ.get("DB_USER", "CCC"),
        password=os.environ.get("APP_PWD", "CccPwd_2026"),
        dsn=os.environ.get("DB_DSN", "localhost:1521/FREEPDB1"),
    )
