# ==============================================
# database/pg.py - JST対応版（Ovv Official）
# ==============================================

import json
from typing import Optional, List
import psycopg2
import psycopg2.extras
from config import POSTGRES_URL

# ----------------------------------------------
# JST 時刻生成
# ----------------------------------------------
from datetime import datetime, timedelta, timezone
JST = timezone(timedelta(hours=9))
def now_jst():
    return datetime.now(JST)

# ----------------------------------------------
# Globals
# ----------------------------------------------
PG_CONN = None
AUDIT_READY = False


# ============================================================
# pg_connect
# ============================================================
def pg_connect():
    """
    PostgreSQL への接続を確立し、PG_CONN をセットする。
    """
    global PG_CONN
    print("=== [PG] Connecting ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL missing")
        PG_CONN = None
        return None

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        conn.autocommit = True
        PG_CONN = conn
        print("[PG] Connected OK")
        return conn
    except Exception as e:
        print("[PG] Connection failed:", repr(e))
        PG_CONN = None
        return None


# ============================================================
# init_db（JST版）
# ============================================================
def init_db(conn):
    global AUDIT_READY
    print("=== [PG] init_db() ===")

    if conn is None:
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        # JST を使うため DEFAULT NOW() は撤廃
        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS ovv;
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                details JSONB,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.thread_brain (
                context_key BIGINT PRIMARY KEY,
                summary JSONB NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            );
        """)

        cur.close()
        AUDIT_READY = True
        print("[PG] init_db OK")

    except Exception as e:
        print("[PG] init_db ERROR:", repr(e))
        AUDIT_READY = False


# ============================================================
# audit_log（JST版）
# ============================================================
def log_audit(event_type: str, details: Optional[dict] = None):
    if details is None:
        details = {}

    print(f"[AUDIT] {event_type} :: {details}")

    if not AUDIT_READY or PG_CONN is None:
        return

    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.audit_log (event_type, details, created_at)
                VALUES (%s, %s::jsonb, %s)
                """,
                (event_type, json.dumps(details), now_jst()),
            )
    except Exception as e:
        print("[AUDIT] write failed:", repr(e))


# ============================================================
# Runtime Memory CRUD（JST版）
# ============================================================
def load_runtime_memory(session_id: str) -> List[dict]:
    if PG_CONN is None:
        return []

    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT memory_json FROM ovv.runtime_memory WHERE session_id = %s",
                (session_id,),
            )
            row = cur.fetchone()
            if row:
                return row["memory_json"]
            return []
    except Exception as e:
        print("[PG] load_runtime_memory ERROR:", repr(e))
        return []


def save_runtime_memory(session_id: str, mem: List[dict]):
    if PG_CONN is None:
        return False

    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (session_id)
                DO UPDATE SET memory_json = EXCLUDED.memory_json,
                              updated_at = EXCLUDED.updated_at
                """,
                (session_id, json.dumps(mem), now_jst()),
            )
        return True
    except Exception as e:
        print("[PG] save_runtime_memory ERROR:", repr(e))
        return False


def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40):
    mem = load_runtime_memory(session_id)
    mem.append({
        "role": role,
        "content": content,
        "created_at": now_jst().isoformat(),
    })

    # 古いものを削除
    if len(mem) > limit:
        mem = mem[-limit:]

    save_runtime_memory(session_id, mem)


# ============================================================
# Thread Brain CRUD（JST）
# ============================================================
def load_thread_brain(context_key: int):
    if PG_CONN is None:
        return None

    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT summary FROM ovv.thread_brain WHERE context_key = %s",
                (context_key,),
            )
            row = cur.fetchone()
            if row:
                return row["summary"]
            return None
    except Exception as e:
        print("[PG] load_thread_brain ERROR:", repr(e))
        return None


def save_thread_brain(context_key: int, summary: dict) -> bool:
    if PG_CONN is None:
        return False

    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.thread_brain (context_key, summary, updated_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (context_key)
                DO UPDATE SET summary = EXCLUDED.summary,
                              updated_at = EXCLUDED.updated_at
                """,
                (context_key, json.dumps(summary), now_jst()),
            )
        return True
    except Exception as e:
        print("[PG] save_thread_brain ERROR:", repr(e))
        return False


# ============================================================
# generate_thread_brain（JST版：meta.updated_at を JST に書き換え）
# ============================================================
def generate_thread_brain(context_key: int, mem: List[dict]):
    """
    ここでは簡易 summary を作成し、meta.updated_at を JST にする。
    """
    summary = {
        "meta": {
            "version": "1.0",
            "updated_at": now_jst().isoformat(),
            "context_key": context_key,
            "total_tokens_estimate": len(mem),
        },
        "status": {"risk": [], "phase": "idle", "last_major_event": ""},
        "decisions": [],
        "unresolved": [],
        "constraints": [],
        "next_actions": [],
        "history_digest": "",
        "high_level_goal": "",
        "recent_messages": mem[-5:],
        "current_position": "",
    }
    return summary
