# database/pg.py
# ============================================================
# Persist v3.0 仕様完全対応版　
# ============================================================

import os
import psycopg2
import psycopg2.extras
from datetime import datetime

# ============================================================
# DB接続
# ============================================================

PG_URL = os.getenv("POSTGRES_URL")
_conn = None


def init_db():
    global _conn

    if _conn is not None:
        return _conn

    if not PG_URL:
        raise RuntimeError("POSTGRES_URL が設定されていません。")

    _conn = psycopg2.connect(PG_URL)
    _conn.autocommit = True
    return _conn


def _execute(sql: str, params=None):
    conn = init_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return None


# ============================================================
# CREATE TABLE — Persist v3.0 正式定義
# ============================================================

CREATE_TABLE_TASK_SESSION = """
CREATE TABLE IF NOT EXISTS task_session (
    task_id TEXT PRIMARY KEY,
    user_id TEXT,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_seconds INTEGER
);
"""

CREATE_TABLE_TASK_LOG = """
CREATE TABLE IF NOT EXISTS task_log (
    id SERIAL PRIMARY KEY,
    task_id TEXT,
    event_type TEXT,
    content TEXT,
    created_at TIMESTAMP
);
"""


def migrate_persist_v3():
    _execute(CREATE_TABLE_TASK_SESSION)
    _execute(CREATE_TABLE_TASK_LOG)


# ============================================================
# INSERT: task_log
# ============================================================

def insert_task_log(task_id: str, event_type: str, content: str, created_at: datetime):
    sql = """
        INSERT INTO task_log (task_id, event_type, content, created_at)
        VALUES (%s, %s, %s, %s);
    """
    _execute(sql, (task_id, event_type, content, created_at))


# ============================================================
# task_start
# ============================================================

def insert_task_session_start(task_id: str, user_id: str, started_at: datetime):
    """
    task_start が押された時に呼ばれる。
    既存レコードがあれば start を上書きし、ended/duration をリセット。
    """

    sql = """
        INSERT INTO task_session (task_id, user_id, started_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (task_id)
        DO UPDATE SET
            user_id = EXCLUDED.user_id,
            started_at = EXCLUDED.started_at,
            ended_at = NULL,
            duration_seconds = NULL;
    """
    _execute(sql, (task_id, user_id, started_at))


# ============================================================
# task_end / task_paused（end + duration 計算）
# ============================================================

def insert_task_session_end_and_duration(task_id: str, ended_at: datetime) -> int | None:
    """
    ended_at を書き込み、duration_seconds を計算して更新する。

    Returns:
        duration_seconds or None
    """

    sql_select = "SELECT started_at FROM task_session WHERE task_id = %s;"
    rows = _execute(sql_select, (task_id,))

    if not rows:
        return None

    started_at = rows[0].get("started_at")
    if not started_at:
        return None

    duration_seconds = int((ended_at - started_at).total_seconds())

    sql_update = """
        UPDATE task_session
        SET ended_at = %s,
            duration_seconds = %s
        WHERE task_id = %s;
    """
    _execute(sql_update, (ended_at, duration_seconds, task_id))

    return duration_seconds


# ============================================================
# 自動マイグレーション
# ============================================================

try:
    migrate_persist_v3()
except Exception as e:
    print("[Persist] Migration failed:", e)