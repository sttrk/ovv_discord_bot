# ovv/external_services/notion/ops/builders.py
# ============================================================
# MODULE CONTRACT: External / NotionOps Builder v3.0
#
# ROLE:
#   - Core の出力 dict を、Notion Executor が必ず処理可能な
#     NotionOps(list[dict]) に変換する唯一のビルダー。
#
# RESPONSIBILITY TAGS:
#   [BUILD_OPS]   Core → NotionOps の形式変換（正規化）
#   [TASK_DB]     TaskDB(name, status, duration, summary) 反映のための命令生成
#   [STRICT]      Core の "mode" を唯一のディスパッチ基準として扱う
#   [SAFE]        None 返却禁止（必ず list を返す）
#
# CONSTRAINTS:
#   - Builder は「命令のフォーマット化のみ」、副作用禁止。
#   - Stabilizer（Persist/augment）と Executor（Notion API）とは厳密に分離する。
#   - free_chat / 不明モードでは空リスト [] を返す。
# ============================================================

from __future__ import annotations
from typing import Any, Dict, List


# ============================================================
# Public Entry
# ============================================================

def build_notion_ops(core_output: Dict[str, Any], request: Any) -> List[Dict[str, Any]]:
    """
    Core の mode に応じて NotionOps(list[dict]) を生成する。

    返り値:
        - list[dict]（空リスト含む）
        - 「None を返さない」のが正式仕様
    """

    mode = core_output.get("mode")
    task_id = getattr(request, "task_id", None)

    # NOTE: user_meta → created_by 変換
    user_meta = getattr(request, "user_meta", {}) or {}
    created_by = user_meta.get("user_name") or user_meta.get("user_id") or ""

    # NotionOps は必ず list で返す
    ops: List[Dict[str, Any]] = []

    # --------------------------------------------------------
    # task_create
    # --------------------------------------------------------
    if mode == "task_create":
        ops.append(
            {
                "op": "task_create",
                "task_id": task_id,
                "task_name": core_output.get("task_name", f"Task {task_id}"),
                "created_by": created_by,
            }
        )
        return ops

    # --------------------------------------------------------
    # task_start
    # --------------------------------------------------------
    if mode == "task_start":
        ops.append(
            {
                "op": "task_start",
                "task_id": task_id,
                "created_by": created_by,
            }
        )
        return ops

    # --------------------------------------------------------
    # task_paused
    #   - summary は builder 側では付与しない。
    #     理由：Stabilizer で duration と共に安全に augment するため。
    # --------------------------------------------------------
    if mode == "task_paused":
        ops.append(
            {
                "op": "task_paused",
                "task_id": task_id,
            }
        )
        return ops

    # --------------------------------------------------------
    # task_end
    #   - duration と summary は Stabilizer が augment する。
    # --------------------------------------------------------
    if mode == "task_end":
        ops.append(
            {
                "op": "task_end",
                "task_id": task_id,
            }
        )
        return ops

    # --------------------------------------------------------
    # その他（free_chat 等）
    # --------------------------------------------------------
    return []