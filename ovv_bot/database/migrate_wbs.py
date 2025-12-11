# ovv_bot/database/migrate_wbs.py

import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

SQL = """
CREATE TABLE IF NOT EXISTS thread_wbs (
    thread_id TEXT PRIMARY KEY,
    wbs_json  TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

def main():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL)
        conn.commit()
        print("[migrate_wbs] thread_wbs table ensured")
    finally:
        conn.close()

if __name__ == "__main__":
    main()