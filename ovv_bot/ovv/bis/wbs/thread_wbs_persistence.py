# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Persistence v1.0 (Minimal)
#
# ROLE:
#   - thread_id ↔ ThreadWBS(JSON text) の永続化を担当する。
#   - 保存（UPSERT）と取得（LOAD）のみを行う。
#
# RESPONSIBILITY TAGS:
#   [PERSIST]   ThreadWBS の DB 永続化
#   [LOAD]      ThreadWBS の取得
#   [MINIMAL]   STEP A 用の最小責務実装
#   [STRICT]    構造解釈・編集・推論を一切行わない
#
# CONSTRAINTS (HARD):
#   - ThreadWBS の構造を解釈しない
#   - CDC / Builder / Interface_Box ロジックを含めない
#   - Persist v3.0 の接続管理に完全追従する
#   - 独自 connection / commit / close を行わない
#   - 1 thread_id = 1 row を厳守する
# ============================================================

from __future__ import annotations

from typing import Optional, Dict, Any
import json
import datetime

from database.pg import init_db


# ============================================================
# Internal Helpers
# ============================================================

def _now_utc() -> datetime.datetime:
    """
    UTC 現在時刻を返す。
    Persist 側の TIMESTAMP と整合させるため naive UTC を使用。
    """
    return datetime.datetime.utcnow()


# ============================================================
# Public API
# ============================================================

def load_thread_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    [LOAD]
    ThreadWBS を取得する。

    Returns:
        - Dict[str, Any]: JSON を復元した WBS
        - None: 未存在 or JSON 破損時
    """
    sql = """
        SELECT wbs_json
        FROM thread_wbs
        WHERE thread_id = %s
        LIMIT 1;
    """

    conn = init_db()
    with conn.cursor() as cur:
        cur.execute(sql, (thread_id,))
        row = cur.fetchone()

        if not row:
            return None

        raw = row[0]
        try:
            return json.loads(raw)
        except Exception:
            # JSON 破損時は破綻回避を優先し None 扱い
            return None


def save_thread_wbs(thread_id: str, wbs_json: Dict[str, Any]) -> None:
    """
    [PERSIST]
    ThreadWBS を保存する。

    動作:
        - row が存在しない場合: INSERT
        - row が存在する場合: UPDATE（上書き）
    """
    raw = json.dumps(wbs_json, ensure_ascii=False)

    sql = """
        INSERT INTO thread_wbs (thread_id, wbs_json, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (thread_id)
        DO UPDATE SET
            wbs_json = EXCLUDED.wbs_json,
            updated_at = EXCLUDED.updated_at;
    """

    conn = init_db()
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                thread_id,
                raw,
                _now_utc(),
            ),
        )