# database/migrate_persist_v3.py
# ============================================================
# [MODULE CONTRACT]
# NAME: migrate_persist_v3
# LAYER: PERSIST (Migration)
#
# ROLE:
#   - Persist v3.0 スキーマ（task_session / task_log）の追加マイグレーション。
#   - 既存テーブル（runtime_memory / thread_brain / audit_log）は変更しない。
#
# INPUT:
#   - 環境変数 DATABASE_URL（pg.py と同じ）
#
# OUTPUT:
#   - PostgreSQL 上に v3.0 用テーブルを作成（存在しない場合のみ）
#
# CONSTRAINT:
#   - database.pg の接続方式に倣う（同じ DATABASE_URL を利用）。
#   - reset 用の migrate_reset.py には手を入れない。
# ============================================================

import sys
import psycopg2
import psycopg2.extras

from database import pg as pg_db  # pg.py と同一接続設定を共有する


# ============================================================
# SQL: Persist v3.0 schema
# ============================================================

SQL_CREATE_TASK_SESSION = """
CREATE TABLE IF NOT EXISTS task_session (
    id                      SERIAL PRIMARY KEY,
    task_id                 TEXT NOT NULL UNIQUE,
    thread_id               TEXT NOT NULL,
    context_key             TEXT NOT NULL,

    title                   TEXT,

    status                  TEXT NOT NULL DEFAULT 'running',

    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at                TIMESTAMPTZ,
    total_seconds           INTEGER,

    tags                    JSONB,

    summary_what            TEXT,
    summary_failure         TEXT,
    summary_countermeasure  TEXT,
    summary_result          TEXT,
    summary_exec_seconds    INTEGER,

    meta                    JSONB,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

SQL_INDEXES_TASK_SESSION = [
    """
    CREATE INDEX IF NOT EXISTS idx_task_session_context_key
        ON task_session (context_key);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_task_session_status
        ON task_session (status);
    """,
]


SQL_CREATE_TASK_LOG = """
CREATE TABLE IF NOT EXISTS task_log (
    id              SERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL,
    logged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind            TEXT NOT NULL,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    context_key     TEXT,
    thread_id       TEXT
);
"""

SQL_INDEXES_TASK_LOG = [
    """
    CREATE INDEX IF NOT EXISTS idx_task_log_task_id
        ON task_log (task_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_task_log_logged_at
        ON task_log (logged_at);
    """,
]


# ============================================================
# Migration entrypoint
# ============================================================

def migrate_persist_v3() -> None:
    """
    Persist v3.0（task_session / task_log）を作成するマイグレーション。
    database.pg の conn を利用し、既存テーブルには触れない。
    """
    conn = pg_db.conn
    if conn is None:
        raise RuntimeError("database.pg.conn が初期化されていません。DATABASE_URL を確認してください。")

    print("[MIGRATE] Persist v3.0 migration start")

    with conn.cursor() as cur:
        # task_session
        print("[MIGRATE] Creating table task_session ...")
        cur.execute(SQL_CREATE_TASK_SESSION)
        for sql in SQL_INDEXES_TASK_SESSION:
            cur.execute(sql)

        # task_log
        print("[MIGRATE] Creating table task_log ...")
        cur.execute(SQL_CREATE_TASK_LOG)
        for sql in SQL_INDEXES_TASK_LOG:
            cur.execute(sql)

    conn.commit()
    print("[MIGRATE] Persist v3.0 migration completed successfully.")


if __name__ == "__main__":
    try:
        migrate_persist_v3()
    except Exception as e:
        print(f"[MIGRATE] Persist v3.0 migration failed: {e}", file=sys.stderr)
        sys.exit(1)