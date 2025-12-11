# ovv/external_services/notion/ops/builders.py
# ============================================================
# MODULE CONTRACT: External / NotionOps Builder v3.0 (Summary Ready)
#
# ROLE:
#   - Core の出力(core_output)を解析し、NotionOps Executor が
#     直接処理できる ops(dict) を生成する。
#
# RESPONSIBILITY TAGS:
#   [BUILD_OPS]  Core → NotionOps の橋渡し
#   [TASK_DB]    TaskDB(name/status/duration/summary) 操作の要求を構築
#   [SUMMARY]    task_paused / task_end 時に summary 更新 OP を追加
#
# CONSTRAINTS:
#   - Stabilizer が list 化 → duration append → Executor へ送る流れを前提とする
#   - Core の mode 以外には反応しない（free_chat などは None を返す）
# ============================================================

from __future__ import annotations
from typing import Any, Dict, Optional


def build_notion_ops(core_output: Dict[str, Any], request: Any) -> Optional[Dict[str, Any]]:
    """
    Core の mode に応じて NotionOps(dict) を生成する。
    Stabilizer によって list 化され、duration ops が追加される。
    """

    mode = core_output.get("mode")
    if mode not in ("task_create", "task_start", "task_paused", "task_end"):
        return None

    # --------------------------------------------------------
    # request からユーザー情報を抽出
    # --------------------------------------------------------
    task_id = getattr(request, "task_id", None)
    user_meta = getattr(request, "user_meta", {}) or {}
    created_by = user_meta.get("user_name") or user_meta.get("user_id") or ""

    # --------------------------------------------------------
    # 基本 OP（mode で分岐）
    # --------------------------------------------------------
    base_ops = {
        "op": mode,
        "task_id": task_id,
        "created_by": created_by,
    }

    # task_create: タスク名を追加
    if mode == "task_create":
        base_ops["task_name"] = core_output.get("task_name", f"Task {task_id}")
        return base_ops

    # --------------------------------------------------------
    # Summary handling (task_paused / task_end)
    # --------------------------------------------------------
    if mode in ("task_paused", "task_end"):
        # ThreadBrain 未実装のため暫定 summary（後続フェーズで TB 要約に差し替える）
        summary_text = core_output.get(
            "summary_text",
            f"[summary:{mode}] auto-generated placeholder for task_id={task_id}",
        )

        return {
            "op": "update_task_summary",
            "task_id": task_id,
            "summary_text": summary_text,
            "created_by": created_by,
        }

    # --------------------------------------------------------
    # task_start（特別処理なし）
    # --------------------------------------------------------
    return base_ops