# database/pg.py
# PostgreSQL Layer – Ovv Persistence v2.0
#
# [MODULE CONTRACT]
# NAME: pg
# ROLE: PERSIST (Layer 5 – Persistence)
#
# INPUT:
#   - runtime_memory append/load
#   - thread_brain generate/load/save
#
# OUTPUT:
#   - dict / list (JSON-serializable structures)
#
# MUST:
#   - DB I/O のみを担当（Core/Interface/Gate へ越境しない）
#   - 全ての永続化はここを経由する
#   - ThreadBrain を破壊しない
#
# MUST_NOT:
#   - Discord API に触れない
#   - Ovv Core ロジックを含めない
#   - Interface Box / Stabilizer を呼ばない
#
# DEPENDENCY:
#   - psycopg2
#   - ovv.bis.memory_kind (kind分類)
#

from __future__ import annotations

import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from ovv.bis.memory_kind import classify_memory_kind


# ============================================================
# PostgreSQL Connection
# ============================================================

PG_URL = os.getenv("POSTGRES_URL")

conn = None


def init_db():
    """Initialize PostgreSQL connection and create tables if not exist."""
    global conn
    conn = psycopg2.connect(PG_URL)
    conn.autocommit = True

    with conn.cursor() as cur:
        # runtime_memory table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL
            );
            """
        )

        # thread_brain table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_brain (
                context_key BIGINT PRIMARY KEY,
                tb_json JSONB NOT NULL
            );
            """
        )


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
    """
    Append a runtime memory entry.
    Each entry now includes "kind": domain / control / system.

    kind:
        - "auto": classify_memory_kind() による自動分類
        - 明示指定: その文字列を優先
    """
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
    """Load stored ThreadBrain summary for a given context."""
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
    """Store ThreadBrain summary."""
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


# ============================================================
# THREAD BRAIN GENERATION (LLM Summarization Layer)
# ============================================================

from ovv.brain.threadbrain_generator import generate_tb_summary


def generate_thread_brain(context_key: int, mem: list) -> Optional[dict]:
    """
    Generate TB summary from runtime memory.
    This calls the TB summarizer (LLM or algorithm).
    """
    if not mem:
        return None

    summary = generate_tb_summary(context_key, mem)
    return summary


# ============================================================
# REPAIR UTILITIES
# ============================================================

def wipe_runtime_memory(session_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM runtime_memory WHERE session_id = %s;",
            (session_id,)
        )


def wipe_thread_brain(context_key: int):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM thread_brain WHERE context_key = %s;",
            (context_key,)
        )


def wipe_all():
    """Dangerous: Clear all persistent memory."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM runtime_memory;")
        cur.execute("DELETE FROM thread_brain;")


# ============================================================
# INIT
# ============================================================
init_db()