# database/pg_wbs.py
# ============================================================
# MODULE CONTRACT: Persist / ThreadWBS Persistence v1.0
#
# ROLE:
#   - thread_id ↔ ThreadWBS(JSON) の永続化
#
# RESPONSIBILITY TAGS:
#   [PERSIST]   WBS JSON の保存/取得
#   [GUARD]     JSON 正規化と例外ガード
#
# CONSTRAINTS:
#   - 構造解釈・推論は行わない
#   - I/O は PG のみ
# ============================================================

from __future__ import annotations

from typing import Optional, Dict, Any
import json
import psycopg2
from psycopg2.extras import DictCursor

from database.pg_connection import get_pg_connection


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def get_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    thread_id に紐づく WBS(JSON) を取得する。
    存在しない場合は None を返す。
    """
    if not thread_id:
        return None

    sql = """
        SELECT wbs_json
        FROM thread_wbs
        WHERE thread_id = %s
        LIMIT 1
    """

    conn = get_pg_connection()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(sql, (thread_id,))
            row = cur.fetchone()
            if not row:
                return None

            try:
                return json.loads(row["wbs_json"])
            except json.JSONDecodeError:
                # 破損時は None（上位で再生成）
                print("[pg_wbs] JSON decode failed for thread_id:", thread_id)
                return None
    finally:
        conn.close()


def save_wbs(thread_id: str, wbs_dict: Dict[str, Any]) -> None:
    """
    WBS(JSON) を UPSERT で保存する。
    """
    if not thread_id or not isinstance(wbs_dict, dict):
        return

    wbs_json = json.dumps(wbs_dict, ensure_ascii=False)

    sql = """
        INSERT INTO thread_wbs (thread_id, wbs_json, created_at, updated_at)
        VALUES (%s, %s, NOW(), NOW())
        ON CONFLICT (thread_id)
        DO UPDATE SET
            wbs_json = EXCLUDED.wbs_json,
            updated_at = NOW()
    """

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (thread_id, wbs_json))
            conn.commit()
    finally:
        conn.close()


def delete_wbs(thread_id: str) -> None:
    """
    thread_id に紐づく WBS を削除する（将来用・通常は使用しない）。
    """
    if not thread_id:
        return

    sql = "DELETE FROM thread_wbs WHERE thread_id = %s"

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (thread_id,))
            conn.commit()
    finally:
        conn.close()