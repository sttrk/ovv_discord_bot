# database/pg.py
# ============================================================
# [MODULE CONTRACT]
# NAME: pg
# LAYER: PERSIST (Layer-5)
#
# ROLE:
#   - runtime_memory / thread_brain / audit_log の永続化を一手に引き受ける。
#
# MUST:
#   - DB I/O のみを担当する（BIS/Gate/Core/Stab に依存しない）
#   - 全ての永続化はここを経由する
#
# MUST NOT:
#   - Discord / Notion / LLM を呼ばない
#   - Interface_Box / Pipeline / Core を import しない
# ============================================================

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import psycopg2
import psycopg2.extras

from ovv.bis.memory_kind import classify_memory_kind
from ovv.brain.threadbrain_generator import generate_tb_summary


PG_URL = os.getenv("POSTGRES_URL")
conn = None  # type: ignore


# ============================================================
# INIT
# ============================================================

def init_db():
    """Initialize PostgreSQL connection and create tables if not exist."""
    global conn
    conn = psycopg2.connect(PG_URL)
    conn.autocommit = True

    with conn.cursor() as cur:
        # runtime_memory
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL
            );
            """
        )

        # thread_brain
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_brain (
                context_key BIGINT PRIMARY KEY,
                tb_json JSONB NOT NULL
            );
            """
        )

        # audit_log（簡易）
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

    print("[PERSIST] init_db OK")


# ============================================================
# RUNTIME MEMORY
# ============================================================

def load_runtime_memory(session_id: str) -> List[dict]:
    if conn is None:
        return []

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            "SELECT memory_json FROM runtime_memory WHERE session_id = %s;",
            (session_id,),
        )
        row = cur.fetchone()
        if row:
            mem = row["memory_json"]
            print(f"[PERSIST] load_runtime_memory: session={session_id}, len={len(mem)}")
            return mem
    print(f"[PERSIST] load_runtime_memory: session={session_id}, (empty)")
    return []


def save_runtime_memory(session_id: str, mem: List[dict]) -> None:
    if conn is None:
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runtime_memory (session_id, memory_json)
            VALUES (%s, %s)
            ON CONFLICT (session_id)
            DO UPDATE SET memory_json = EXCLUDED.memory_json;
            """,
            (session_id, json.dumps(mem)),
        )
    print(f"[PERSIST] save_runtime_memory: session={session_id}, len={len(mem)}")


def append_runtime_memory(
    session_id: str,
    role: str,
    content: str,
    limit: int = 40,
    kind: str = "auto",
) -> None:
    mem = load_runtime_memory(session_id)

    if kind == "auto":
        kind_value = classify_memory_kind(role=role, content=content)
    else:
        kind_value = kind

    mem.append(
        {
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind_value,
        }
    )

    if len(mem) > limit:
        mem = mem[-limit:]

    save_runtime_memory(session_id, mem)


# ============================================================
# THREAD BRAIN
# ============================================================

def load_thread_brain(context_key: int) -> Optional[dict]:
    if conn is None:
        return None

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            "SELECT tb_json FROM thread_brain WHERE context_key = %s;",
            (context_key,),
        )
        row = cur.fetchone()
        if row:
            tb = row["tb_json"]
            print(f"[PERSIST] load_thread_brain: ctx={context_key}, exists=True")
            return tb
    print(f"[PERSIST] load_thread_brain: ctx={context_key}, exists=False")
    return None


def save_thread_brain(context_key: int, tb_json: dict) -> None:
    if conn is None:
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO thread_brain (context_key, tb_json)
            VALUES (%s, %s)
            ON CONFLICT (context_key)
            DO UPDATE SET tb_json = EXCLUDED.tb_json;
            """,
            (context_key, json.dumps(tb_json)),
        )
    print(f"[PERSIST] save_thread_brain: ctx={context_key}")


def generate_thread_brain(context_key: int, mem: list) -> Optional[dict]:
    if not mem:
        print(f"[PERSIST] generate_thread_brain: ctx={context_key}, mem empty")
        return None

    try:
        summary = generate_tb_summary(context_key, mem)
        print(f"[PERSIST] generate_thread_brain: ctx={context_key}, ok={bool(summary)}")
        return summary
    except Exception as e:
        print("[PERSIST] generate_thread_brain ERROR:", repr(e))
        return None


# ============================================================
# REPAIR UTILITIES
# ============================================================

def wipe_runtime_memory(session_id: str):
    if conn is None:
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM runtime_memory WHERE session_id = %s;", (session_id,))
    print(f"[PERSIST] wipe_runtime_memory: session={session_id}")


def wipe_thread_brain(context_key: int):
    if conn is None:
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM thread_brain WHERE context_key = %s;", (context_key,))
    print(f"[PERSIST] wipe_thread_brain: ctx={context_key}")


def wipe_all():
    if conn is None:
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM runtime_memory;")
        cur.execute("DELETE FROM thread_brain;")
    print("[PERSIST] wipe_all: runtime_memory + thread_brain cleared")


# ============================================================
# AUDIT LOG
# ============================================================

def log_audit(event_type: str, payload: Dict[str, Any]) -> None:
    if conn is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_log (event_type, payload)
            VALUES (%s, %s);
            """,
            (event_type, json.dumps(payload)),
        )
    print(f"[PERSIST] log_audit: {event_type}")


# ============================================================
# INIT
# ============================================================

init_db()