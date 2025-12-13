# ovv/intent/repository.py
from __future__ import annotations

from typing import List
from datetime import datetime

from database.pg import _execute
from .types import Intent


def save_intent(intent: Intent) -> None:
    sql = """
        INSERT INTO intent_log (
            intent_id,
            context_key,
            raw_text,
            state,
            created_at,
            meta_json
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (intent_id)
        DO NOTHING
    """
    _execute(
        sql,
        (
            intent.intent_id,
            intent.context_key,
            intent.raw_text,
            intent.state,
            intent.created_at,
            intent.meta,
        ),
    )


def update_intent_state(intent_id: str, state: str) -> None:
    sql = """
        UPDATE intent_log
        SET state = %s
        WHERE intent_id = %s
    """
    _execute(sql, (state, intent_id))


def find_recent_by_context(context_key: str, limit: int = 20) -> List[Intent]:
    sql = """
        SELECT intent_id, context_key, raw_text, state, created_at, meta_json
        FROM intent_log
        WHERE context_key = %s
        ORDER BY created_at DESC
        LIMIT %s
    """
    rows = _execute(sql, (context_key, limit)) or []

    intents: List[Intent] = []
    for r in rows:
        intents.append(
            Intent(
                intent_id=r["intent_id"],
                context_key=r["context_key"],
                raw_text=r["raw_text"],
                state=r["state"],
                created_at=r["created_at"],
                meta=r.get("meta_json") or {},
            )
        )
    return intents