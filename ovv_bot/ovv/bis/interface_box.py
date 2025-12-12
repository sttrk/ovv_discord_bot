# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.10 (CDC Runtime Lock + WBS Finalize)
#
# ROLE:
#   - Boundary_Gate から渡された InputPacket を受け取り、
#     Core → NotionOps Builder → Stabilizer の実行順序を保証する。
#   - ThreadWBS を「推論前コンテキスト」として Core に渡す。
#   - 明示コマンドに限り、ThreadWBS の最小更新を発火させる。
#   - CDC による work_item 候補は Runtime のみに保持し、
#     !wy / !wn / !we でのみ確定・破棄する。
#   - work_item の finalize（done/dropped）は !wd / !wx のみで確定し、
#     finalized_item は Stabilizer(thread_state) に引き渡す。
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
    mark_focus_done,
    mark_focus_dropped,
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
    meta = getattr(packet, "meta", None) or {}
    for k in ("discord_thread_name", "discord_channel_name", "thread_name", "channel_name"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return str(getattr(packet, "channel_id", "") or "untitled-task")


def _format_wbs_brief(wbs: Dict[str, Any], max_items: int = 10) -> str:
    task = wbs.get("task", "")
    status = wbs.get("status", "")
    focus = wbs.get("focus_point", None)
    items = wbs.get("work_items", []) or []

    lines = []
    lines.append("[WBS]")
    lines.append(f"- task: {task}")
    lines.append(f"- status: {status}")
    lines.append(f"- focus_point: {focus}")

    if not items:
        lines.append("- work_items: (empty)")
        return "\n".join(lines)

    lines.append("- work_items:")
    for i, it in enumerate(items[:max_items]):
        rationale = ""
        st = ""
        if isinstance(it, dict):
            rationale = (it.get("rationale") or "").strip()
            st = (it.get("status") or "").strip()
        if not rationale:
            rationale = "<no rationale>"
        if st:
            lines.append(f"  - [{i}] ({st}) {rationale}")
        else:
            lines.append(f"  - [{i}] {rationale}")

    if len(items) > max_items:
        lines.append(f"  ... ({len(items) - max_items} more)")

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
#   returns: (updated_wbs, hint, finalized_item)
# ============================================================

def _apply_wbs_update(
    command: Optional[str],
    thread_id: Optional[str],
    packet: InputPacket,
    wbs: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[Dict[str, Any]]]:

    if not command or not thread_id:
        return wbs, None, None

    # ---- !t ----
    if command == "task_create":
        if wbs is not None:
            return wbs, "[WBS] already exists.", None
        wbs = create_empty_wbs(_get_thread_name(packet))
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] initialized.", None

    # ---- !tp ----
    if command == "task_paused":
        if not wbs:
            return wbs, "[WBS] not found; pause skipped.", None
        wbs = on_task_pause(wbs)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] paused.", None

    # ---- !tc ----
    if command == "task_end":
        if not wbs:
            return wbs, "[WBS] not found; complete skipped.", None
        wbs = on_task_complete(wbs)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] completed.", None

    # ---- !wbs / !w ----
    if command == "wbs_show":
        if not wbs:
            return wbs, "[WBS] not found.", None
        return wbs, _format_wbs_brief(wbs), None

    # ---- !wy ----
    if command == "wbs_accept":
        if not wbs:
            return wbs, "[WBS] not found; run !t first.", None
        cand = _pop_cdc_candidate(thread_id)
        if not cand:
            return wbs, "[CDC] no candidate.", None
        wbs = accept_work_item(wbs, {"rationale": cand.get("rationale", "")})
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] work_item accepted.", None

    # ---- !wn ----
    if command == "wbs_reject":
        cand = _pop_cdc_candidate(thread_id)
        if not cand:
            return wbs, "[CDC] no candidate; nothing to reject.", None
        return wbs, "[CDC] candidate rejected.", None

    # ---- !we ----
    if command == "wbs_edit":
        if not wbs:
            return wbs, "[WBS] not found; run !t first.", None
        cand = _peek_cdc_candidate(thread_id)
        if not cand:
            return wbs, "[CDC] no candidate.", None
        new_text = (packet.content or "").strip()
        if not new_text:
            return wbs, "[CDC] edit text required. Usage: !we <new rationale>", None
        _pop_cdc_candidate(thread_id)
        wbs = edit_and_accept_work_item(wbs, {"rationale": cand.get("rationale", "")}, new_text)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] edited & accepted.", None

    # ---- !wd ----
    if command == "wbs_done":
        if not wbs:
            return wbs, "[WBS] not found; run !t first.", None
        wbs, finalized = mark_focus_done(wbs)
        if not finalized:
            save_thread_wbs(thread_id, wbs)
            return wbs, "[WBS] no focus_point; nothing done.", None
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] focus work_item marked as done.", finalized

    # ---- !wx ----
    if command == "wbs_drop":
        if not wbs:
            return wbs, "[WBS] not found; run !t first.", None
        reason = (packet.content or "").strip() or None
        wbs, finalized = mark_focus_dropped(wbs, reason=reason)
        if not finalized:
            save_thread_wbs(thread_id, wbs)
            return wbs, "[WBS] no focus_point; nothing dropped.", None
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] focus work_item marked as dropped.", finalized

    return wbs, None, None


# ============================================================
# ENTRY
# ============================================================

async def handle_request(packet: InputPacket) -> str:
    command = packet.command
    thread_id = _select_thread_id(packet.task_id, packet.context_key)

    # 1) WBS load（失敗は握る）
    wbs: Optional[Dict[str, Any]] = None
    try:
        if thread_id:
            wbs = load_thread_wbs(thread_id)
    except Exception as e:
        print("[Interface_Box:WARN] failed to load ThreadWBS:", repr(e))
        wbs = None

    # 2) WBS explicit update（明示コマンドのみ）
    wbs_hint: Optional[str] = None
    finalized_item: Optional[Dict[str, Any]] = None
    try:
        wbs, wbs_hint, finalized_item = _apply_wbs_update(command, thread_id, packet, wbs)
    except Exception as e:
        print("[Interface_Box:WARN] failed to update ThreadWBS:", repr(e))

    # 3) Core dispatch（WBS は参照コンテキストとして渡す）
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

    # 4) CDC candidate store（task_create のみ、Runtime only）
    cdc_hint: Optional[str] = None
    try:
        if command == "task_create" and thread_id:
            cdc = core_output.get("cdc_candidate")
            if isinstance(cdc, dict):
                r = cdc.get("rationale")
                if isinstance(r, str) and r.strip():
                    cdc_hint = _store_cdc_candidate(thread_id, r)
    except Exception as e:
        print("[Interface_Box:WARN] failed to store CDC candidate:", repr(e))

    # 5) message compose（上書き禁止：追記のみ）
    message = core_output.get("message_for_user", "") or ""
    for h in (wbs_hint, cdc_hint):
        if isinstance(h, str) and h.strip():
            message = f"{message}\n\n{h}" if message else h

    # 6) NotionOps builder
    notion_ops = build_notion_ops(core_output, request=_PacketProxy(packet))

    # 7) Stabilizer
    #    NOTE: wbs_done / wbs_drop は Core.mode が free_chat になり得るため、
    #          Stabilizer には command を優先して渡す（summary 発火のため）。
    command_type_for_stabilizer = core_output.get("mode") or command

    thread_state: Dict[str, Any] = {}
    if wbs:
        thread_state["thread_wbs"] = wbs
    if finalized_item:
        thread_state["finalized_item"] = finalized_item

    stabilizer = Stabilizer(
        message_for_user=message,
        notion_ops=notion_ops,
        context_key=packet.context_key,
        user_id=packet.author_id,
        task_id=packet.task_id,
        command_type=command_type_for_stabilizer,
        core_output=core_output,
        thread_state=thread_state if thread_state else None,
    )

    return await stabilizer.finalize()


class _PacketProxy:
    def __init__(self, packet: InputPacket):
        self.task_id = packet.task_id
        self.user_meta = packet.user_meta
        self.context_key = packet.context_key
        self.meta = packet.meta

    def __repr__(self) -> str:
        return f"<PacketProxy task_id={self.task_id}>"