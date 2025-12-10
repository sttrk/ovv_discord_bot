"""
NotionOps Builder
Core の出力と BIS Packet を基に NotionOps(dict) を組み立てる。
"""

from typing import Any, Dict, Optional


def build_notion_ops(core_output: Dict[str, Any], packet: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Core の結果から Notion DB に反映する内容を組み立てる。
    Persist v3.0 以降、task_id は packet["task_id"] と一致する。

    戻り値:
      None → Notion 操作なし
      dict → executor に渡す命令群
    """

    if not isinstance(core_output, dict):
        return None

    # Core 側で決めたモードを優先
    core_mode = core_output.get("core_mode") or core_output.get("mode")
    if core_mode not in ("task_create", "task_start", "task_end"):
        return None

    # BIS Packet から task_id / user 情報を取得
    if not isinstance(packet, dict):
        return None

    task_id = packet.get("task_id")
    if not task_id:
        # Notion 側でタスク紐付けが出来ないので中止
        return None

    user_meta = packet.get("user_meta") or {}
    user_name = user_meta.get("user_name")
    user_id = user_meta.get("user_id")
    created_by = user_name or (str(user_id) if user_id is not None else "")

    # Discord 向けメッセージをログとして残す
    msg = (
        core_output.get("message_for_user")
        or core_output.get("reply_text")
        or ""
    )

    return {
        "op": core_mode,        # "task_create" / "task_start" / "task_end"
        "task_id": str(task_id),
        "created_by": created_by,
        "core_message": msg,
    }