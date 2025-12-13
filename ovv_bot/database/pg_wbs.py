# database/pg_wbs.py
# ============================================================
# MODULE CONTRACT: Persist / ThreadWBS Persistence v2.1
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
#   - DB スキーマ差異を吸収し、Core を失敗させない
# ============================================================

from __future__ import annotations

from typing import Optional, Dict, Any
import json

from database.pg import _execute


# ============================================================
# Public API (Core 契約準拠)
# ============================================================

def load_thread_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    thread_id に紐づく WBS(JSON) を取得する。
    """
    if not thread_id:
        return None

    sql = """
        SELECT wbs_json
        FROM thread_wbs
        WHERE thread_id = %s
        LIMIT 1
    """

    rows = _execute(sql, (thread_id,))
    if not rows:
        return None

    raw = rows[0].get("wbs_json")
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("[Persist][thread_wbs] JSON decode failed:", thread_id)
        return None


def save_thread_wbs(thread_id: str, wbs: Dict[str, Any]) -> None:
    """
    WBS(JSON) を UPSERT で保存する。
    created_at / updated_at は **存在しない DB でも動作する**
    """
    if not thread_id or not isinstance(wbs, dict):
        return

    wbs_json = json.dumps(wbs, ensure_ascii=False)

    # 最小構成での UPSERT（既存 DB と完全互換）
    sql = """
        INSERT INTO thread_wbs (thread_id, wbs_json)
        VALUES (%s, %s)
        ON CONFLICT (thread_id)
        DO UPDATE SET
            wbs_json = EXCLUDED.wbs_json
    """

    _execute(sql, (thread_id, wbs_json))


def wipe_thread_wbs(thread_id: str) -> None:
    """
    thread_id に紐づく WBS を削除する（debug / reset 用）。
    """
    if not thread_id:
        return

    sql = "DELETE FROM thread_wbs WHERE thread_id = %s"
    _execute(sql, (thread_id,))