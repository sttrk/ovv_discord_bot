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
#   [COMPAT]    Core v1.x 互換 API 提供
#
# CONSTRAINTS:
#   - 構造解釈・推論は行わない
#   - 接続管理は database.pg に委譲
# ============================================================

from __future__ import annotations

from typing import Optional, Dict, Any
import json

from database.pg import _execute


# ============================================================
# Migration
# ============================================================

CREATE_TABLE_THREAD_WBS = """
CREATE TABLE IF NOT EXISTS thread_wbs (
    thread_id TEXT PRIMARY KEY,
    wbs_json TEXT NOT NULL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
"""

try:
    _execute(CREATE_TABLE_THREAD_WBS)
except Exception as e:
    print("[Persist][thread_wbs] migration failed:", e)


# ============================================================
# Core API (Canonical)
# ============================================================

def load_thread_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
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
    if not thread_id or not isinstance(wbs, dict):
        return

    wbs_json = json.dumps(wbs, ensure_ascii=False)

    sql = """
        INSERT INTO thread_wbs (thread_id, wbs_json, created_at, updated_at)
        VALUES (%s, %s, NOW(), NOW())
        ON CONFLICT (thread_id)
        DO UPDATE SET
            wbs_json = EXCLUDED.wbs_json,
            updated_at = NOW()
    """
    _execute(sql, (thread_id, wbs_json))


def wipe_thread_wbs(thread_id: str) -> None:
    if not thread_id:
        return

    sql = "DELETE FROM thread_wbs WHERE thread_id = %s"
    _execute(sql, (thread_id,))


# ============================================================
# Backward Compatibility Layer (IMPORTANT)
# ============================================================

# Core v1.x compatibility
def save_wbs(thread_id: str, wbs: Dict[str, Any]) -> None:
    save_thread_wbs(thread_id, wbs)


def load_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
    return load_thread_wbs(thread_id)