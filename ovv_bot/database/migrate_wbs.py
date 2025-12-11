# ovv_bot/database/migrate_wbs.py

import os
import psycopg2

POSTGRES_URL = os.getenv("POSTGRES_URL")
if not POSTGRES_URL:
    raise RuntimeError("POSTGRES_URL is not set")

SQL = """
CREATE TABLE IF NOT EXISTS thread_wbs (
    thread_id TEXT PRIMARY KEY,
    wbs_json  TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

def main():
    conn = psycopg2.connect(POSTGRES_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL)
        conn.commit()
        print("[migrate_wbs] thread_wbs table ensured")
    finally:
        conn.close()

if __name__ == "__main__":
    main()