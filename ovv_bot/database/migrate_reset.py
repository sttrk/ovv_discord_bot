# ============================================================
# PG Migration Reset Script
# 目的：
#   - runtime_memory / thread_brain を DROP
#   - bot.py 起動前にスキーマをリセットする
# ============================================================

import os
import psycopg2
import psycopg2.extras

def pg_connect():
    url = os.getenv("POSTGRES_URL")
    if not url:
        raise RuntimeError("POSTGRES_URL not found")
    return psycopg2.connect(url, sslmode="require")

def run():
    print("=== [MIGRATE] Connecting to PostgreSQL ===")
    conn = pg_connect()
    cur = conn.cursor()

    # DROP TABLES
    drop_sql = [
        "DROP TABLE IF EXISTS runtime_memory;",
        "DROP TABLE IF EXISTS thread_brain;",
    ]

    for sql in drop_sql:
        print(f"[MIGRATE] Executing: {sql.strip()}")
        cur.execute(sql)

    conn.commit()
    cur.close()
    conn.close()

    print("=== [MIGRATE] Completed successfully ===")

if __name__ == "__main__":
    run()
