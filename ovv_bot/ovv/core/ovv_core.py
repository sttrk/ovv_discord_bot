# ovv/core/ovv_core.py
# ============================================================
# MODULE CONTRACT: CORE / OvvCore v1.4 (FIXED)
#
# ROLE:
#   - InputPacket を受け取り、ThreadWBS / Persist / NotionOps を構成し、
#     Stabilizer に返す「Core 統合制御層」。
#
# RESPONSIBILITY TAGS:
#   [CORE]     command dispatch / orchestration
#   [WBS]      ThreadWBS の生成・更新（builder に委譲）
#   [PERSIST]  PG I/O は adapter 経由（直接 SQL しない）
#   [NOTION]   Notion ops の生成のみ（実行は executor）
#   [DEBUG]    Debugging Subsystem v1.0（観測のみ）
#
# CONSTRAINTS:
#   - 推論しない
#   - thread_id を UI 名に使わない（命名は CDC 済み title）
#   - 初期命名 CDC は ThreadWBS builder の create_empty_wbs に集約
#   - context_splitter は初期段階では使用しない
#   - Notion Task 名は必ず CDC 済み title（wbs["task"]）を用いる
#   - NotionOps Builder は core_output["mode"] と core_output["task_title"] を参照する
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List

from ovv.bis.types import InputPacket
from ovv.bis.wbs import thread_wbs_builder as wbs_builder

# Persist adapter（正規APIのみ使用）
from database import pg_wbs

# Notion ops builder
from ovv.external_services.notion.ops.builders import build_notion_ops


# ============================================================
# Result
# ============================================================

@dataclass
class CoreResult:
    """
    Stabilizer に返す統合結果（最小）。
    """
    discord_output: str
    notion_ops: Optional[List[Dict[str, Any]]] = None
    wbs: Optional[Dict[str, Any]] = None
    core_output: Optional[Dict[str, Any]] = None


# ============================================================
# Safe helpers
# ============================================================

def _safe_meta_thread_name(packet: InputPacket) -> str:
    meta = getattr(packet, "meta", None)
    if isinstance(meta, dict):
        v = meta.get("discord_thread_name")
        if isinstance(v, str):
            return v
    return ""


def _load_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    Persist / ThreadWBS のロード（正規API）。
    """
    try:
        return pg_wbs.load_thread_wbs(thread_id)
    except Exception:
        return None


def _save_wbs(thread_id: str, wbs: Dict[str, Any]) -> None:
    """
    Persist / ThreadWBS の保存（正規API）。
    """
    pg_wbs.save_thread_wbs(thread_id, wbs)


def _mk_core_output(
    *,
    mode: str,
    task_title: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"mode": mode}
    if task_title is not None:
        out["task_title"] = task_title
    if isinstance(extra, dict) and extra:
        out.update(extra)
    return out


# ============================================================
# Core entry
# ============================================================

def handle_packet(packet: InputPacket) -> CoreResult:
    cmd = getattr(packet, "command", None)
    if not isinstance(cmd, str):
        return CoreResult(
            discord_output="Command missing.",
            notion_ops=[],
            core_output=_mk_core_output(mode="free_chat"),
        )

    if cmd == "task_create":
        return _cmd_task_create(packet)
    if cmd == "wbs_show":
        return _cmd_wbs_show(packet)
    if cmd == "task_paused":
        return _cmd_task_pause(packet)
    if cmd == "task_end":
        return _cmd_task_complete(packet)
    if cmd == "wbs_accept":
        return _cmd_wbs_accept(packet)
    if cmd == "wbs_edit":
        return _cmd_wbs_edit_accept(packet)
    if cmd == "wbs_done":
        return _cmd_wbs_done(packet)
    if cmd == "wbs_drop":
        return _cmd_wbs_drop(packet)

    return CoreResult(
        discord_output=f"Unknown command: {cmd}",
        notion_ops=[],
        core_output=_mk_core_output(mode="free_chat"),
    )


# ============================================================
# Commands
# ============================================================

def _cmd_task_create(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    raw_thread_name = _safe_meta_thread_name(packet)
    trace_id = getattr(packet, "trace_id", None)

    existing = _load_wbs(thread_id)
    if isinstance(existing, dict) and existing.get("task"):
        title = str(existing.get("task") or "")
        return CoreResult(
            discord_output=f"Task already exists: {title}",
            notion_ops=[],
            wbs=existing,
            core_output=_mk_core_output(mode="task_create", task_title=title),
        )

    wbs = wbs_builder.create_empty_wbs(raw_thread_name, trace_id=trace_id)
    title = str(wbs.get("task") or "")

    _save_wbs(thread_id, wbs)

    core_output = _mk_core_output(mode="task_create", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult(
        discord_output=f"Task created: {title}",
        notion_ops=notion_ops,
        wbs=wbs,
        core_output=core_output,
    )


def _cmd_wbs_show(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(
            discord_output="WBS not found. Run !t to create.",
            notion_ops=[],
            core_output=_mk_core_output(mode="free_chat"),
        )

    return CoreResult(
        discord_output=_format_wbs(wbs),
        notion_ops=[],
        wbs=wbs,
        core_output=_mk_core_output(mode="free_chat"),
    )


def _cmd_task_pause(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", [])

    wbs = wbs_builder.on_task_pause(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    title = str(wbs.get("task") or "")
    core_output = _mk_core_output(mode="task_paused", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult("Task paused.", notion_ops, wbs, core_output)


def _cmd_task_complete(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", [])

    wbs = wbs_builder.on_task_complete(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    title = str(wbs.get("task") or "")
    core_output = _mk_core_output(mode="task_end", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult("Task completed.", notion_ops, wbs, core_output)


def _cmd_wbs_accept(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", [])

    candidate = {"rationale": str(getattr(packet, "content", "") or "")}
    wbs = wbs_builder.accept_work_item(wbs, candidate, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    return CoreResult("Work item accepted.", [], wbs, _mk_core_output(mode="free_chat"))


def _cmd_wbs_edit_accept(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", [])

    rationale = str(getattr(packet, "content", "") or "").strip()
    wbs = wbs_builder.edit_and_accept_work_item(
        wbs, {}, rationale, trace_id=getattr(packet, "trace_id", None)
    )
    _save_wbs(thread_id, wbs)

    return CoreResult("Work item edited+accepted.", [], wbs, _mk_core_output(mode="free_chat"))


def _cmd_wbs_done(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", [])

    wbs, finalized = wbs_builder.mark_focus_done(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    if not finalized:
        return CoreResult("No focus item to finalize.", [], wbs, _mk_core_output(mode="free_chat"))

    return CoreResult("Focus item marked done.", [], wbs, _mk_core_output(mode="free_chat"))


def _cmd_wbs_drop(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", [])

    reason = str(getattr(packet, "content", "") or "").strip() or None
    wbs, finalized = wbs_builder.mark_focus_dropped(
        wbs, reason, trace_id=getattr(packet, "trace_id", None)
    )
    _save_wbs(thread_id, wbs)

    if not finalized:
        return CoreResult("No focus item to finalize.", [], wbs, _mk_core_output(mode="free_chat"))

    return CoreResult("Focus item dropped.", [], wbs, _mk_core_output(mode="free_chat"))


# ============================================================
# Formatting
# ============================================================

def _format_wbs(wbs: Dict[str, Any]) -> str:
    task = str(wbs.get("task") or "")
    status = str(wbs.get("status") or "")
    focus = wbs.get("focus_point")
    items = wbs.get("work_items") if isinstance(wbs.get("work_items"), list) else []

    lines = [
        "=== ThreadWBS ===",
        f"task   : {task}",
        f"status : {status}",
        f"focus  : {focus}",
        "",
        "[work_items]",
    ]
    for i, it in enumerate(items):
        if isinstance(it, dict):
            r = str(it.get("rationale", "") or "")
            st = str(it.get("status", "") or "")
            label = f"- {i}: {r}"
            if st:
                label += f" [{st}]"
            lines.append(label)
        else:
            lines.append(f"- {i}: {str(it)}")

    return "```\n" + "\n".join(lines) + "\n```"