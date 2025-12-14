# ovv/core/ovv_core.py
# ============================================================
# MODULE CONTRACT: CORE / OvvCore v1.4.1 (STABLE + free_chat + wbs_show_full)
#
# CHANGELOG:
#   - v1.4.1:
#       - Boundary_Gate v3.8.2 対応
#       - "wbs_show_full" を追加（stable + volatile の可視化）
#       - Core は volatile を「表示のみ」扱う（編集責務は持たない）
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Callable

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


def _thread_id(packet: InputPacket) -> str:
    return str(getattr(packet, "context_key", "") or "")


def _load_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
    try:
        return pg_wbs.load_thread_wbs(thread_id)
    except Exception:
        return None


def _save_wbs(thread_id: str, wbs: Dict[str, Any]) -> None:
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


def _require_wbs(packet: InputPacket) -> Optional[Dict[str, Any]]:
    tid = _thread_id(packet)
    if not tid:
        return None
    return _load_wbs(tid)


def _title_from_wbs(wbs: Dict[str, Any]) -> str:
    return str(wbs.get("task") or "")


def _empty_ops() -> List[Dict[str, Any]]:
    return []


# ============================================================
# Core entry
# ============================================================

def handle_packet(packet: InputPacket) -> CoreResult:
    cmd = getattr(packet, "command", None)
    if not isinstance(cmd, str) or not cmd:
        return CoreResult(
            discord_output="Command missing.",
            notion_ops=_empty_ops(),
            core_output=_mk_core_output(mode="free_chat"),
        )

    # ---- Dispatch table (deterministic) ----
    dispatch: Dict[str, Callable[[InputPacket], CoreResult]] = {
        "task_create": _cmd_task_create,
        "task_start": _cmd_task_start,
        "wbs_show": _cmd_wbs_show,
        "wbs_show_full": _cmd_wbs_show_full,  # ★ NEW
        "task_paused": _cmd_task_pause,
        "task_end": _cmd_task_complete,
        "wbs_accept": _cmd_wbs_accept,
        "wbs_edit": _cmd_wbs_edit_accept,
        "wbs_done": _cmd_wbs_done,
        "wbs_drop": _cmd_wbs_drop,
        "free_chat": _cmd_free_chat,
    }

    fn = dispatch.get(cmd)
    if fn is None:
        return CoreResult(
            discord_output=f"Unknown command: {cmd}",
            notion_ops=_empty_ops(),
            core_output=_mk_core_output(mode="free_chat"),
        )

    return fn(packet)


# ============================================================
# Commands
# ============================================================

def _cmd_task_create(packet: InputPacket) -> CoreResult:
    thread_id = _thread_id(packet)
    raw_thread_name = _safe_meta_thread_name(packet)
    trace_id = getattr(packet, "trace_id", None)

    existing = _load_wbs(thread_id)
    if isinstance(existing, dict) and existing.get("task"):
        title = _title_from_wbs(existing)
        return CoreResult(
            discord_output=f"Task already exists: {title}",
            notion_ops=_empty_ops(),
            wbs=existing,
            core_output=_mk_core_output(mode="task_create", task_title=title),
        )

    wbs = wbs_builder.create_empty_wbs(raw_thread_name, trace_id=trace_id)
    title = _title_from_wbs(wbs)

    _save_wbs(thread_id, wbs)

    core_output = _mk_core_output(mode="task_create", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult(
        discord_output=f"Task created: {title}",
        notion_ops=notion_ops,
        wbs=wbs,
        core_output=core_output,
    )


def _cmd_task_start(packet: InputPacket) -> CoreResult:
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", _empty_ops())

    title = _title_from_wbs(wbs)
    core_output = _mk_core_output(mode="task_start", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult("Task started.", notion_ops, wbs, core_output)


def _cmd_wbs_show(packet: InputPacket) -> CoreResult:
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t to create.", _empty_ops())

    return CoreResult(
        discord_output=_format_wbs(wbs, include_volatile=False),
        notion_ops=_empty_ops(),
        wbs=wbs,
        core_output=_mk_core_output(mode="free_chat"),
    )


def _cmd_wbs_show_full(packet: InputPacket) -> CoreResult:
    """
    !wbs+
    - stable + volatile の全体可視化
    - 編集・推論は行わない（観測専用）
    """
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t to create.", _empty_ops())

    return CoreResult(
        discord_output=_format_wbs(wbs, include_volatile=True),
        notion_ops=_empty_ops(),
        wbs=wbs,
        core_output=_mk_core_output(
            mode="free_chat",
            task_title=_title_from_wbs(wbs),
            extra={"view": "wbs_full"},
        ),
    )


def _cmd_task_pause(packet: InputPacket) -> CoreResult:
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", _empty_ops())

    wbs = wbs_builder.on_task_pause(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(_thread_id(packet), wbs)

    title = _title_from_wbs(wbs)
    core_output = _mk_core_output(mode="task_paused", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult("Task paused.", notion_ops, wbs, core_output)


def _cmd_task_complete(packet: InputPacket) -> CoreResult:
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", _empty_ops())

    wbs = wbs_builder.on_task_complete(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(_thread_id(packet), wbs)

    title = _title_from_wbs(wbs)
    core_output = _mk_core_output(mode="task_end", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult("Task completed.", notion_ops, wbs, core_output)


def _cmd_wbs_accept(packet: InputPacket) -> CoreResult:
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", _empty_ops())

    candidate = {"rationale": str(getattr(packet, "content", "") or "")}
    wbs = wbs_builder.accept_work_item(wbs, candidate, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(_thread_id(packet), wbs)

    return CoreResult("Work item accepted.", _empty_ops(), wbs, _mk_core_output(mode="free_chat"))


def _cmd_wbs_edit_accept(packet: InputPacket) -> CoreResult:
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", _empty_ops())

    rationale = str(getattr(packet, "content", "") or "").strip()
    wbs = wbs_builder.edit_and_accept_work_item(
        wbs, {}, rationale, trace_id=getattr(packet, "trace_id", None)
    )
    _save_wbs(_thread_id(packet), wbs)

    return CoreResult("Work item edited+accepted.", _empty_ops(), wbs, _mk_core_output(mode="free_chat"))


def _cmd_wbs_done(packet: InputPacket) -> CoreResult:
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", _empty_ops())

    wbs, finalized = wbs_builder.mark_focus_done(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(_thread_id(packet), wbs)

    if not finalized:
        return CoreResult("No focus item to finalize.", _empty_ops(), wbs, _mk_core_output(mode="free_chat"))

    return CoreResult(
        "Focus item marked done.",
        _empty_ops(),
        wbs,
        _mk_core_output(mode="free_chat", extra={"finalized_item": finalized}),
    )


def _cmd_wbs_drop(packet: InputPacket) -> CoreResult:
    wbs = _require_wbs(packet)
    if not wbs:
        return CoreResult("WBS not found. Run !t first.", _empty_ops())

    reason = str(getattr(packet, "content", "") or "").strip() or None
    wbs, finalized = wbs_builder.mark_focus_dropped(
        wbs, reason, trace_id=getattr(packet, "trace_id", None)
    )
    _save_wbs(_thread_id(packet), wbs)

    if not finalized:
        return CoreResult("No focus item to finalize.", _empty_ops(), wbs, _mk_core_output(mode="free_chat"))

    return CoreResult(
        "Focus item dropped.",
        _empty_ops(),
        wbs,
        _mk_core_output(mode="free_chat", extra={"finalized_item": finalized}),
    )


def _cmd_free_chat(packet: InputPacket) -> CoreResult:
    thread_id = _thread_id(packet)
    wbs = _load_wbs(thread_id) if thread_id else None

    user_text = str(getattr(packet, "raw", "") or "").strip()

    reply = ""
    try:
        from ovv.inference.inference_box import ask  # type: ignore
        reply = str(ask(packet=packet, wbs=wbs) or "").strip()
    except Exception:
        reply = ""

    if not reply:
        reply = (
            "free_chat received.\n"
            "- 現段階は推論箱が未接続、または無応答です。\n"
            "（次: inference_box 実装で UI版Ovv に近づけます）"
        )

    core_output = _mk_core_output(
        mode="free_chat",
        task_title=_title_from_wbs(wbs) if isinstance(wbs, dict) else None,
        extra={"user_text": user_text},
    )

    return CoreResult(
        discord_output=reply,
        notion_ops=_empty_ops(),
        wbs=wbs if isinstance(wbs, dict) else None,
        core_output=core_output,
    )


# ============================================================
# Formatting
# ============================================================

def _format_wbs(wbs: Dict[str, Any], *, include_volatile: bool) -> str:
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

    if include_volatile:
        vol = wbs.get("volatile", {})
        lines.extend([
            "",
            "[volatile]",
            f"- intent : {vol.get('intent')}",
            f"- drafts : {len(vol.get('drafts', []))}",
            f"- open_questions : {len(vol.get('open_questions', []))}",
        ])

    return "```\n" + "\n".join(lines) + "\n```"