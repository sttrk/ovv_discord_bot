# database/runtime_memory.py
import json
from typing import List
from datetime import datetime, timezone

import psycopg2.extras

from .pg import PG_CONN


def load_runtime_memory(session_id: str) -> List[dict]:
    """
    1 セッション分の runtime_memory を取得。
    見つからなければ空配列。
    """
    if PG_CONN is None:
        return []
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT memory_json
                FROM ovv.runtime_memory
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return []
            return row["memory_json"]
    except Exception as e:
        print("[runtime_memory load error]", repr(e))
        return []


def save_runtime_memory(session_id: str, mem: List[dict]):
    """
    runtime_memory を upsert。
    """
    if PG_CONN is None:
        return
    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (session_id)
                DO UPDATE SET
                    memory_json = EXCLUDED.memory_json,
                    updated_at  = NOW();
                """,
                (session_id, json.dumps(mem, ensure_ascii=False)),
            )
    except Exception as e:
        print("[runtime_memory save error]", repr(e))


def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40):
    """
    runtime_memory に 1 メッセージ追加（ローテ付き）。
    """
    mem = load_runtime_memory(session_id)
    mem.append(
        {
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    if len(mem) > limit:
        mem = mem[-limit:]
    save_runtime_memory(session_id, mem)
