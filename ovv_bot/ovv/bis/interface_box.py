# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.9.1 (CDC Runtime Lock — Spec Fixed)
#
# ROLE:
#   - Boundary_Gate から渡された InputPacket を受け取り、
#     Core → NotionOps Builder → Stabilizer の実行順序を保証する。
#   - ThreadWBS を「推論前コンテキスト」として Core に渡す。
#   - 明示コマンドに限り、ThreadWBS の最小更新を発火させる。
#   - CDC による work_item 候補は Runtime のみに保持し、
#     !wy / !wn / !we でのみ確定・破棄する。
#
# HARD GUARANTEES:
#   - CDC 候補の永続化は禁止（Runtime only）
#   - LLM による自動改変は禁止
#   - WBS 更新は明示コマンドのみ
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timezone

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
)

# ============================================================
# STEP A: CDC Candidate Runtime Store
#   - 明示的に「プロセス内メモリのみ」
#   - 1 thread_id = 1 candidate
# ============================================================

_RUNTIME_CDC: Dict[str, Dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _format_wbs_brief(wbs: Dict[str, Any]) -> str:
    lines = [
        "[WBS]",
        f"- task: {wbs.get('task')}",
        f"- status: {wbs.get('status')}",
        f"- focus_point: {wbs.get('focus_point')}",
    ]
    items = wbs.get("work_items") or []
    if not items:
        lines.append("- work_items: (empty)")
    else:
        lines.append("- work_items:")
        for i, it in enumerate(items):
            lines.append(f"  - [{i}] {it.get('rationale', '')}")
    return "\n".join(lines)


# ============================================================
# CDC Runtime Ops
# ============================================================

def _store_cdc_candidate(thread_id: str, rationale: str) -> str:
    if thread_id in _RUNTIME_CDC:
        return "[CDC] candidate already exists; use !wy / !wn / !we."
    _RUNTIME_CDC[thread_id] = {
        "rationale": rationale.strip(),
        "generated_at": _now_iso(),
    }
    return "[CDC] candidate generated. Reply with !wy / !wn / !we."


def _pop_cdc_candidate(thread_id: str) -> Optional[Dict[str, Any]]:
    return _RUNTIME_CDC.pop(thread_id, None)


def _peek_cdc_candidate(thread_id: str) -> Optional[Dict[str, Any]]:
    return _RUNTIME_CDC.get(thread_id)


# ============================================================
# WBS Explicit Update
# ============================================================

def _apply_wbs_update(
    command: Optional[str],
    thread_id: Optional[str],
    packet: InputPacket,
    wbs: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:

    if not command or not thread_id:
        return wbs, None

    if command == "task_create":
        if wbs is not None:
            return wbs, "[WBS] already exists."
        wbs = create_empty_wbs(_get_thread_name(packet))
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] initialized."

    if command == "task_paused" and wbs:
        wbs = on_task_pause(wbs)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] paused."

    if command == "task_end" and wbs:
        wbs = on_task_complete(wbs)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] completed."

    if command == "wbs_show" and wbs:
        return wbs, _format_wbs_brief(wbs)

    if command == "wbs_accept" and wbs:
        cand = _pop_cdc_candidate(thread_id)
        if not cand:
            return wbs, "[CDC] no candidate."
        wbs = accept_work_item(wbs, cand)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] work_item accepted."

    if command == "wbs_reject":
        _pop_cdc_candidate(thread_id)
        return wbs, "[CDC] candidate rejected."

    if command == "wbs_edit" and wbs:
        cand = _peek_cdc_candidate(thread_id)
        if not cand:
            return wbs, "[CDC] no candidate."
        new_text = packet.content.strip()
        if not new_text:
            return wbs, "[CDC] edit text required."
        _pop_cdc_candidate(thread_id)
        wbs = edit_and_accept_work_item(wbs, cand, new_text)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] edited & accepted."

    return wbs, None


# ============================================================
# ENTRY
# ============================================================

async def handle_request(packet: InputPacket) -> str:
    command = packet.command
    thread_id = _select_thread_id(packet.task_id, packet.context_key)

    wbs = load_thread_wbs(thread_id) if thread_id else None
    wbs, wbs_hint = _apply_wbs_update(command, thread_id, packet, wbs)

    core_input = {
        "command_type": command,
        "raw_text": packet.raw,
        "arg_text": packet.content,
        "task_id": packet.task_id,
        "context_key": packet.context_key,
        "user_id": packet.author_id,
        "thread_wbs": wbs,
    }

    core_output = run_core(core_input)

    cdc_hint = None
    if command == "task_create" and thread_id:
        cdc = core_output.get("cdc_candidate")
        if isinstance(cdc, dict):
            r = cdc.get("rationale")
            if isinstance(r, str) and r.strip():
                cdc_hint = _store_cdc_candidate(thread_id, r)

    message = core_output.get("message_for_user", "")
    for h in (wbs_hint, cdc_hint):
        if h:
            message = f"{message}\n\n{h}" if message else h

    notion_ops = build_notion_ops(core_output, request=_PacketProxy(packet))

    stabilizer = Stabilizer(
        message_for_user=message,
        notion_ops=notion_ops,
        context_key=packet.context_key,
        user_id=packet.author_id,
        task_id=packet.task_id,
        command_type=core_output.get("mode"),
        core_output=core_output,
        thread_state={"thread_wbs": wbs} if wbs else None,
    )

    return await stabilizer.finalize()


class _PacketProxy:
    def __init__(self, packet: InputPacket):
        self.task_id = packet.task_id
        self.user_meta = packet.user_meta
        self.context_key = packet.context_key
        self.meta = packet.meta