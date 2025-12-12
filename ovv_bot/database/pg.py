# database/pg.py
# ============================================================
# Persist v3.0 仕様完全対応版 (Trace-Aware / Debugging Subsystem v1.0 friendly)
#
# UPDATE POINTS (additive / non-breaking):
#   - debug/debug_commands.py 互換のため `conn` を公開（_conn の alias）
#   - task_log / task_session に trace_id カラムを追加（NULL許容 / 既存コード破壊なし）
#   - insert_task_log に trace_id を任意引数として追加（既存呼び出しはそのまま動作）
#   - init_db の再接続耐性（closed 判定）
# ============================================================

from __future__ import annotations

import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Any, Optional


# ============================================================
# DB接続
# ============================================================

PG_URL = os.getenv("POSTGRES_URL")

_conn = None  # internal
conn = None   # public alias (debug/debug_commands.py compatibility)


def init_db():
    """
    Persist v3.0 の唯一の接続獲得口。
    - 既存接続が生きていれば再利用
    - 切断されていれば再接続
    - autocommit=True（本プロジェクト方針）
    """
    global _conn, conn

    if _conn is not None:
        try:
            # psycopg2 connection has `.closed` (0=open, nonzero=closed)
            if getattr(_conn, "closed", 1) == 0:
                conn = _conn
                return _conn
        except Exception:
            # 判定不能なら作り直す
            _conn = None

    if not PG_URL:
        raise RuntimeError("POSTGRES_URL が設定されていません。")

    _conn = psycopg2.connect(PG_URL)
    _conn.autocommit = True
    conn = _conn
    return _conn


def _execute(sql: str, params: Any = None):
    """
    最小の SQL 実行ヘルパ。
    - fetchall できない文は None を返す
    - 接続は init_db() に追従
    """
    c = init_db()
    with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return None


# ============================================================
# CREATE TABLE — Persist v3.0 正式定義（+ additive columns）
# ============================================================

CREATE_TABLE_TASK_SESSION = """
CREATE TABLE IF NOT EXISTS task_session (
    task_id TEXT PRIMARY KEY,
    user_id TEXT,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_seconds INTEGER,
    trace_id TEXT
);
"""

CREATE_TABLE_TASK_LOG = """
CREATE TABLE IF NOT EXISTS task_log (
    id SERIAL PRIMARY KEY,
    task_id TEXT,
    event_type TEXT,
    content TEXT,
    created_at TIMESTAMP,
    trace_id TEXT
);
"""


# 既存 DB に対して trace_id を後付けする（破壊しない）
ALTER_TASK_SESSION_ADD_TRACE_ID = """
ALTER TABLE IF EXISTS task_session
ADD COLUMN IF NOT EXISTS trace_id TEXT;
"""

ALTER_TASK_LOG_ADD_TRACE_ID = """
ALTER TABLE IF EXISTS task_log
ADD COLUMN IF NOT EXISTS trace_id TEXT;
"""


def migrate_persist_v3() -> None:
    """
    Persist v3.0 の最小マイグレーション。
    - CREATE TABLE（未作成なら作成）
    - 既存表には trace_id カラムを追加（IF NOT EXISTS）
    """
    _execute(CREATE_TABLE_TASK_SESSION)
    _execute(CREATE_TABLE_TASK_LOG)

    # additive migration
    _execute(ALTER_TASK_SESSION_ADD_TRACE_ID)
    _execute(ALTER_TASK_LOG_ADD_TRACE_ID)


# ============================================================
# INSERT: task_log
# ============================================================

def insert_task_log(
    task_id: str,
    event_type: str,
    content: str,
    created_at: datetime,
    trace_id: Optional[str] = None,
):
    """
    task_log への追記。

    Debugging Subsystem v1.0 friendly:
      - trace_id は任意（NULL 許容）
      - 既存呼び出し（4引数）を壊さない
    """
    sql = """
        INSERT INTO task_log (task_id, event_type, content, created_at, trace_id)
        VALUES (%s, %s, %s, %s, %s);
    """
    _execute(sql, (task_id, event_type, content, created_at, trace_id))


# ============================================================
# task_start
# ============================================================

def insert_task_session_start(task_id: str, user_id: str, started_at: datetime, trace_id: Optional[str] = None):
    """
    task_start が押された時に呼ばれる。
    既存レコードがあれば start を上書きし、ended/duration をリセット。

    trace_id は任意（観測用途 / NULL 許容）。
    """
    sql = """
        INSERT INTO task_session (task_id, user_id, started_at, trace_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (task_id)
        DO UPDATE SET
            user_id = EXCLUDED.user_id,
            started_at = EXCLUDED.started_at,
            ended_at = NULL,
            duration_seconds = NULL,
            trace_id = EXCLUDED.trace_id;
    """
    _execute(sql, (task_id, user_id, started_at, trace_id))


# ============================================================
# task_end / task_paused（end + duration 計算）
# ============================================================

def insert_task_session_end_and_duration(task_id: str, ended_at: datetime, trace_id: Optional[str] = None) -> int | None:
    """
    ended_at を書き込み、duration_seconds を計算して更新する。

    Returns:
        duration_seconds or None

    NOTE:
      - started_at が無い場合は None（破綻回避）
      - trace_id は任意（観測用途 / NULL 許容）
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
            duration_seconds = %s,
            trace_id = COALESCE(%s, trace_id)
        WHERE task_id = %s;
    """
    _execute(sql_update, (ended_at, duration_seconds, trace_id, task_id))

    return duration_seconds


# ============================================================
# 自動マイグレーション
# ============================================================

try:
    migrate_persist_v3()
except Exception as e:
    print("[Persist] Migration failed:", e)