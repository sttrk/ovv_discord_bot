# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Persistence v1.0 (Minimal)
#
# ROLE:
#   - thread_id ↔ ThreadWBS(JSON text) の永続化
#   - WBS の「保存 / 取得」のみを責務とする
#
# RESPONSIBILITY TAGS:
#   [PERSIST]   PostgreSQL への保存・取得
#   [STRICT]    ロジックを持たない（Builder 非依存）
#   [MINIMAL]   STEP2 用の最小実装
#
# CONSTRAINTS:
#   - ThreadWBS の構造を解釈しない
#   - CDC / Builder / IFACE ロジックを含めない
#   - 1 thread_id = 1 row
# ============================================================

from __future__ import annotations

from typing import Optional, Dict, Any
import json
import datetime

from database.pg import get_connection


# ============================================================
# Internal helpers
# ============================================================

def _now_utc():
    return datetime.datetime.utcnow()


# ============================================================
# Public API
# ============================================================

def load_thread_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    ThreadWBS を取得する。
    存在しない場合は None を返す。
    """
    sql = """
        SELECT wbs_json
        FROM thread_wbs
        WHERE thread_id = %s
        LIMIT 1
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (thread_id,))
            row = cur.fetchone()

            if not row:
                return None

            raw = row[0]
            try:
                return json.loads(raw)
            except Exception:
                # JSON 破損時は None 扱い（破綻回避）
                return None


def save_thread_wbs(thread_id: str, wbs_json: Dict[str, Any]) -> None:
    """
    ThreadWBS を保存する。
    既存 row があれば上書き、なければ INSERT。
    """
    raw = json.dumps(wbs_json, ensure_ascii=False)

    sql = """
        INSERT INTO thread_wbs (thread_id, wbs_json, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (thread_id)
        DO UPDATE SET
            wbs_json = EXCLUDED.wbs_json,
            updated_at = EXCLUDED.updated_at
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    thread_id,
                    raw,
                    _now_utc(),
                ),
            )
        conn.commit()