# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.10 (WBS Finalize Wired)
#
# ROLE:
#   - Boundary_Gate から渡された InputPacket を受け取り、
#     Core → NotionOps Builder → Stabilizer の実行順序を保証する。
#   - ThreadWBS を推論前コンテキストとして Core に渡す。
#   - 明示コマンドに限り ThreadWBS を更新する。
#   - work_item の done / dropped を NotionTaskSummary に接続する。
#
# HARD GUARANTEES:
#   - CDC 候補は Runtime のみ
#   - LLM による自動改変は禁止
#   - WBS 更新は明示コマンドのみ
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ovv.core.ovv_core import run_core
from ovv.external_services.notion.ops.builders import build_notion_ops
from .stabilizer import Stabilizer
from .types import InputPacket

from ovv.bis.wbs.thread_wbs_persistence import load_thread_wbs, save_thread_wbs
from ovv.bis.wbs.thread_wbs_builder import (
    create_empty_wbs,
    accept_work_item,
    edit_and_accept_work_item,
    on_task_pause,
    on_task_complete,
    mark_focus_done,
    mark_focus_dropped,
)

# ============================================================
# CDC Runtime Store
# ============================================================

_RUNTIME_CDC: Dict[str, Dict[str, Any]] = {}


# ============================================================
# Helpers
# ============================================================

def _select_thread_id(task_id: Any, context_key: Any) -> Optional[str]:
    if task_id:
        return str(task_id)
    if context_key is not None:
        return str(context_key)
    return None


def _get_thread_name(packet: InputPacket) -> str:
    meta = packet.meta or {}
    for k in ("discord_thread_name", "discord_channel_name"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return str(packet.channel_id or "untitled-task")


# ============================================================
# WBS Update + Finalize
# ============================================================

def _apply_wbs_update(
    command: Optional[str],
    thread_id: Optional[str],
    packet: InputPacket,
    wbs: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[Dict[str, Any]]]:
    """
    Returns:
      - updated_wbs
      - user_hint
      - finalized_item (done / dropped のみ)
    """
    finalized_item = None

    if not command or not thread_id:
        return wbs, None, None

    # !t
    if command == "task_create":
        if wbs is not None:
            return wbs, "[WBS] already exists.", None
        wbs = create_empty_wbs(_get_thread_name(packet))
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] initialized.", None

    # !tp
    if command == "task_paused" and wbs:
        wbs = on_task_pause(wbs)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] paused.", None

    # !tc
    if command == "task_end" and wbs:
        wbs = on_task_complete(wbs)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] completed.", None

    # !wy
    if command == "wbs_accept" and wbs:
        cand = _RUNTIME_CDC.pop(thread_id, None)
        if not cand:
            return wbs, "[CDC] no candidate.", None
        wbs = accept_work_item(wbs, cand)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] work_item accepted.", None

    # !we
    if command == "wbs_edit" and wbs:
        cand = _RUNTIME_CDC.pop(thread_id, None)
        if not cand:
            return wbs, "[CDC] no candidate.", None
        new_text = packet.content.strip()
        if not new_text:
            return wbs, "[CDC] edit text required.", None
        wbs = edit_and_accept_work_item(wbs, cand, new_text)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] edited & accepted.", None

    # !wd → DONE
    if command == "wbs_done" and wbs:
        wbs, finalized_item = mark_focus_done(wbs)
        if finalized_item:
            save_thread_wbs(thread_id, wbs)
            return wbs, "[WBS] work_item done.", finalized_item
        return wbs, "[WBS] no active work_item.", None

    # !wx → DROPPED
    if command == "wbs_drop" and wbs:
        reason = packet.content.strip() or None
        wbs, finalized_item = mark_focus_dropped(wbs, reason)
        if finalized_item:
            save_thread_wbs(thread_id, wbs)
            return wbs, "[WBS] work_item dropped.", finalized_item
        return wbs, "[WBS] no active work_item.", None

    return wbs, None, None


# ============================================================
# ENTRY
# ============================================================

async def handle_request(packet: InputPacket) -> str:
    command = packet.command
    thread_id = _select_thread_id(packet.task_id, packet.context_key)

    wbs = load_thread_wbs(thread_id) if thread_id else None
    wbs, wbs_hint, finalized_item = _apply_wbs_update(
        command, thread_id, packet, wbs
    )

    core_output = run_core(
        {
            "command_type": command,
            "raw_text": packet.raw,
            "arg_text": packet.content,
            "task_id": packet.task_id,
            "context_key": packet.context_key,
            "user_id": packet.author_id,
            "thread_wbs": wbs,
        }
    )

    message = core_output.get("message_for_user", "")
    if wbs_hint:
        message = f"{message}\n\n{wbs_hint}" if message else wbs_hint

    notion_ops = build_notion_ops(core_output, request=_PacketProxy(packet))

    stabilizer = Stabilizer(
        message_for_user=message,
        notion_ops=notion_ops,
        context_key=packet.context_key,
        user_id=packet.author_id,
        task_id=packet.task_id,
        command_type=core_output.get("mode"),
        core_output=core_output,
        thread_state={
            "thread_wbs": wbs,
            "finalized_item": finalized_item,
        }
        if finalized_item
        else {"thread_wbs": wbs},
    )

    return await stabilizer.finalize()


class _PacketProxy:
    def __init__(self, packet: InputPacket):
        self.task_id = packet.task_id
        self.user_meta = packet.user_meta
        self.context_key = packet.context_key
        self.meta = packet.meta