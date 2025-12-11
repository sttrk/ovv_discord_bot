# ovv/external_services/notion/ops/builders.py
# ============================================================
# MODULE CONTRACT: External / NotionOps Builder v2.0
#
# ROLE:
#   - Core の出力 dict を、Notion Executor が処理できる
#     NotionOps(list[dict]) に正規化する。
#
# RESPONSIBILITY TAGS:
#   [BUILD_OPS]   Core → NotionOps の形式変換
#   [TASK_DB]     (task_create / start / paused / end / summary)
#   [STRICT]      Core の "mode" を唯一のディスパッチ基準とする
#
# CONSTRAINTS:
#   - 戻り値は list[dict]（Executor が必ず処理可能）
#   - None を返さない（空リスト [] を返す）
#   - Core → Stabilizer → Executor の 3 層分離を守る
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ============================================================
# Public entry
# ============================================================

def build_notion_ops(core_output: Dict[str, Any], request: Any) -> List[Dict[str, Any]]:
    """
    Core の mode に応じて NotionOps(list[dict]) を生成する。

    返り値：必ず list[dict]
      - ops が 0 件 → []
      - 1 件 → [ {...} ]
      - 将来の拡張で複数 ops を返すことも可能
    """

    mode = core_output.get("mode")
    task_id = getattr(request, "task_id", None)
    user_meta = getattr(request, "user_meta", {}) or {}
    created_by = user_meta.get("user_name") or user_meta.get("user_id") or ""

    # NotionOps は list のみを返す
    ops_list: List[Dict[str, Any]] = []

    # ========================================================
    # Task creation
    # ========================================================
    if mode == "task_create":
        ops_list.append(
            {
                "op": "task_create",
                "task_id": task_id,
                "task_name": core_output.get("task_name", f"Task {task_id}"),
                "created_by": created_by,
            }
        )
        return ops_list

    # ========================================================
    # Task start
    # ========================================================
    if mode == "task_start":
        ops_list.append(
            {
                "op": "task_start",
                "task_id": task_id,
                "created_by": created_by,
            }
        )
        return ops_list

    # ========================================================
    # Task paused
    # （サマリ更新のトリガは Stabilizer → Executor 側で行う）
    # ========================================================
    if mode == "task_paused":
        ops_list.append(
            {
                "op": "task_paused",
                "task_id": task_id,
            }
        )
        return ops_list

    # ========================================================
    # Task end
    # ========================================================
    if mode == "task_end":
        ops_list.append(
            {
                "op": "task_end",
                "task_id": task_id,
            }
        )
        return ops_list

    # ========================================================
    # その他（free_chat 等）
    # ========================================================
    return []  # Notion 更新不要