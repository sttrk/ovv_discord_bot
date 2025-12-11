# ovv/external_services/notion/ops/builders.py
"""
NotionOps Builder — Core 出力を Notion Executor 用の命令に整形する
"""

from typing import Any, Dict, Optional


def build_notion_ops(core_output: Dict[str, Any], request: Any) -> Optional[Dict[str, Any]]:
    """
    Core の mode に応じて Notion に送る命令を生成。
    """

    mode = core_output.get("mode")

    if mode not in ("task_create", "task_start", "task_paused", "task_end"):
        return None

    task_id = getattr(request, "task_id", None)
    user_meta = getattr(request, "user_meta", {}) or {}
    created_by = user_meta.get("user_name") or user_meta.get("user_id") or ""

    ops = {
        "op": mode,
        "task_id": task_id,
        "created_by": created_by,
    }

    if mode == "task_create":
        ops["task_name"] = core_output.get("task_name", f"Task {task_id}")

    return ops