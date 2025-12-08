# database/pg.py
# PostgreSQL Layer – Ovv Persistence v2.1 Stable
#
# [MODULE CONTRACT]
# NAME: pg
# ROLE: PERSIST (Layer 5 – Persistence)
#
# RESPONSIBILITY:
#   - runtime_memory append/load
#   - thread_brain load/save/generate
#   - audit_log (system monitoring)
#
# MUST:
#   - DB I/O のみを担当（Core/Interface/Gate へ越境禁止）
#   - 全永続化はこの層で統一
#
# MUST NOT:
#   - Discord API に触れない
#   - Core ロジックを含めない
#   - Interface_Box / Stabilizer を呼ばない

from __future__ import annotations

import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from ovv.bis.memory_kind import classify_memory_kind
from ovv.brain.threadbrain_generator import generate_tb_summary


# ============================================================
# PostgreSQL Connection Helpers
# ============================================================

PG_URL = os.getenv("POSTGRES_URL")

conn = None


def pg_connect():
    """Return a NEW PostgreSQL connection (safe for short-lived ops)."""
    return psycopg2.connect(PG_URL)


def init_db():
    """Initialize PostgreSQL and create required tables if missing."""
    global conn
    conn = pg_connect()
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

        # thread_brain summary
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_brain (
                context_key BIGINT PRIMARY KEY,
                tb_json JSONB NOT NULL
            );
            """
        )

        # audit_log
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """
        )


# ============================================================
# AUDIT LOG (復元)
# ============================================================

def log_audit(event_type: str, payload: dict):
    """Record system events for debugging / monitoring."""
    try:
        with pg_connect() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_log (event_type, payload)
                    VALUES (%s, %s);
                    """,
                    (event_type, json.dumps(payload, ensure_ascii=False)),
                )
    except Exception as e:
        print("[log_audit] error:", repr(e))


# ============================================================
# RUNTIME MEMORY
# ============================================================

def load_runtime_memory(session_id: str) -> List[dict]:
    """Load runtime_memory list for a given session_id."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            "SELECT memory_json FROM runtime_memory WHERE session_id = %s;",
            (session_id,),
        )
        row = cur.fetchone()
        if row:
            return row["memory_json"]
    return []


def save_runtime_memory(session_id: str, mem: List[dict]) -> None:
    """Save runtime_memory list."""
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


def append_runtime_memory(
    session_id: str,
    role: str,
    content: str,
    limit: int = 40,
    kind: str = "auto",
) -> None:
    """Append a new memory entry with auto-kind classification."""
    mem = load_runtime_memory(session_id)

    kind_value = (
        classify_memory_kind(role=role, content=content)
        if kind == "auto"
        else kind
    )

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
# THREAD BRAIN Summaries
# ============================================================

def load_thread_brain(context_key: int) -> Optional[dict]:
    """Load TB summary for a given context."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            "SELECT tb_json FROM thread_brain WHERE context_key = %s;",
            (context_key,),
        )
        row = cur.fetchone()
        if row:
            return row["tb_json"]
    return None


def save_thread_brain(context_key: int, tb_json: dict) -> None:
    """Persist ThreadBrain."""
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


def generate_thread_brain(context_key: int, mem: list) -> Optional[dict]:
    """Generate TB summary via LLM summarizer."""
    if not mem:
        return None
    return generate_tb_summary(context_key, mem)


# ============================================================
# REPAIR UTILITIES
# ============================================================

def wipe_runtime_memory(session_id: str):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM runtime_memory WHERE session_id = %s;", (session_id,))


def wipe_thread_brain(context_key: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM thread_brain WHERE context_key = %s;", (context_key,))


def wipe_all():
    """Dangerous: clears ALL persistent memory."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM runtime_memory;")
        cur.execute("DELETE FROM thread_brain;")
        cur.execute("DELETE FROM audit_log;")


# ============================================================
# INIT
# ============================================================
init_db()