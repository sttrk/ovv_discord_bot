"""
NotionOps Builder
Core の出力と Request(InputPacket) を基に NotionOps(dict) を組み立てる。
"""

from typing import Any, Dict, Optional


def build_notion_ops(core_output: Dict[str, Any], request: Any) -> Optional[Dict[str, Any]]:
    """
    Core の結果から Notion DB に反映する内容を組み立てる。
    Persist v3.0 以降、task_id は request.task_id と一致する。

    戻り値:
      None → Notion 操作なし
      dict → executor に渡す命令群
    """

    # task_create / task_start / task_end 以外では Notion ops を出さない
    command_type = core_output.get("mode")
    if command_type not in ("task_create", "task_start", "task_end"):
        return None

    # request(InputPacket) の情報が必要
    task_id = getattr(request, "task_id", None)
    user_meta = getattr(request, "user_meta", {})
    created_by = user_meta.get("user_name") or user_meta.get("user_id")

    if not task_id:
        return None

    # Core の message_for_user のログも送る
    msg = core_output.get("message_for_user", "")

    return {
        "op": command_type,      # "task_create" / "task_start" / "task_end"
        "task_id": task_id,
        "created_by": created_by,
        "core_message": msg,
    }