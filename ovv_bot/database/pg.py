# database/pg.py
# ============================================================
# Persist v3.0 仕様完全対応版 + Pause/Resume Duration Support
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


# ============================================================
# SQL ヘルパ
# ============================================================

def _execute(sql: str, params=None):
    conn = init_db()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return None


# ============================================================
# Persist v3.0 テーブル
#   task_session
#   task_log
#   + pause/resume 用 last_started_at
# ============================================================

CREATE_TABLE_TASK_SESSION = """
CREATE TABLE IF NOT EXISTS task_session (
    task_id TEXT PRIMARY KEY,
    user_id TEXT,
    started_at TIMESTAMP,
    last_started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_seconds INTEGER
);
"""

ALTER_TABLE_TASK_SESSION_ADD_LAST_STARTED_AT = """
ALTER TABLE task_session
ADD COLUMN IF NOT EXISTS last_started_at TIMESTAMP;
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
    _execute(ALTER_TABLE_TASK_SESSION_ADD_LAST_STARTED_AT)
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
# SESSION: task_start / resume
# ============================================================

def insert_task_session_start(task_id: str, user_id: str, started_at: datetime):
    """
    task_start（開始 or 再開）で呼ばれる。

    モデル:
      - started_at:
          そのタスクが「最初に」開始された時刻（初回 start でのみセット）
      - last_started_at:
          直近の「稼働開始」時刻（start または resume 毎に更新）
      - ended_at:
          完全終了時刻（completed 時にセット）
      - duration_seconds:
          これまでの累積稼働秒数（pause / end 時に加算）
    """

    # 既存セッションを確認
    sql_select = """
        SELECT task_id, user_id, started_at, last_started_at, ended_at, duration_seconds
        FROM task_session
        WHERE task_id = %s;
    """
    rows = _execute(sql_select, (task_id,))
    row = rows[0] if rows else None

    if row is None:
        # 新規タスク開始
        sql_insert = """
            INSERT INTO task_session (task_id, user_id, started_at, last_started_at, ended_at, duration_seconds)
            VALUES (%s, %s, %s, %s, NULL, %s);
        """
        _execute(sql_insert, (task_id, user_id, started_at, started_at, 0))
        return

    # 既存レコードあり
    existing_started = row.get("started_at")
    existing_ended = row.get("ended_at")

    if existing_ended is not None:
        # すでに completed 済み → 新規セッションとしてリセット
        sql_update = """
            UPDATE task_session
            SET user_id = %s,
                started_at = %s,
                last_started_at = %s,
                ended_at = NULL,
                duration_seconds = %s
            WHERE task_id = %s;
        """
        _execute(sql_update, (user_id, started_at, started_at, 0, task_id))
        return

    # 進行中 or paused 中
    # started_at が空なら初回開始とみなす
    new_started = existing_started or started_at

    sql_update_resume = """
        UPDATE task_session
        SET user_id = %s,
            started_at = %s,
            last_started_at = %s
        WHERE task_id = %s;
    """
    _execute(sql_update_resume, (user_id, new_started, started_at, task_id))


# ============================================================
# SESSION: pause + duration 累積
# ============================================================

def insert_task_session_pause_and_duration(task_id: str, paused_at: datetime) -> int | None:
    """
    task_paused で呼ばれる。
    現在の last_started_at から paused_at までの差分を duration_seconds に加算し、
    last_started_at を NULL にする（非稼働状態）。

    Returns
    -------
    duration_seconds : int | None
        更新後の duration_seconds（累積）。行が無い/last_started_at 無しの場合は None。
    """

    sql_select = """
        SELECT started_at, last_started_at, ended_at, duration_seconds
        FROM task_session
        WHERE task_id = %s;
    """
    rows = _execute(sql_select, (task_id,))
    if not rows:
        return None

    row = rows[0]
    last_started_at = row.get("last_started_at")
    if not last_started_at:
        # すでに paused 中、または開始前 → 加算なし
        return row.get("duration_seconds")

    base = row.get("duration_seconds") or 0
    delta = int((paused_at - last_started_at).total_seconds())
    new_duration = base + max(delta, 0)

    sql_update = """
        UPDATE task_session
        SET last_started_at = NULL,
            duration_seconds = %s
        WHERE task_id = %s;
    """
    _execute(sql_update, (new_duration, task_id))

    return new_duration


# ============================================================
# SESSION: end + duration 累積
# ============================================================

def insert_task_session_end_and_duration(task_id: str, ended_at: datetime) -> int | None:
    """
    task_end で呼ばれる。
    SESSION の duration を計算して更新する（pause/resume を含めた累積）。

    モデル:
      - last_started_at が残っていれば「稼働中」とみなし、
        ended_at までの差分を duration_seconds に加算してから終了。
      - すでに paused 済みなら last_started_at は NULL のはずで、
        duration_seconds はそれまでの累積値。

    Returns
    -------
    duration_seconds : int | None
        更新後の duration_seconds（累積）。行が無い場合は None。
    """

    sql_select = """
        SELECT started_at, last_started_at, ended_at, duration_seconds
        FROM task_session
        WHERE task_id = %s;
    """
    rows = _execute(sql_select, (task_id,))
    if not rows:
        return None

    row = rows[0]
    last_started_at = row.get("last_started_at")
    base = row.get("duration_seconds") or 0

    if last_started_at is not None:
        delta = int((ended_at - last_started_at).total_seconds())
        base += max(delta, 0)

    new_duration = base

    sql_update = """
        UPDATE task_session
        SET ended_at = %s,
            last_started_at = NULL,
            duration_seconds = %s
        WHERE task_id = %s;
    """
    _execute(sql_update, (ended_at, new_duration, task_id))

    return new_duration


# 初回ロード時にテーブルを作成
try:
    migrate_persist_v3()
except Exception as e:
    print("[Persist] Migration failed:", e)