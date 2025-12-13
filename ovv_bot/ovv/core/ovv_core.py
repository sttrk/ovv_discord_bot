# ovv/core/ovv_core.py
# ============================================================
# MODULE CONTRACT: CORE / OvvCore v1.3
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
#   - 推論しない（LLM を呼ばない）
#   - thread_id を UI 名に使わない（命名は CDC 済み title）
#   - 初期命名 CDC は ThreadWBS builder の create_empty_wbs に集約
#   - context_splitter は初期段階では使用しない
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List

# NOTE: パスはあなたのリポジトリ構造に合わせている
from ovv.bis.types import InputPacket
from ovv.bis.wbs import thread_wbs_builder as wbs_builder

# Persist adapters
from database import pg_wbs
from database import runtime_memory

# Notion ops (builder only; executor is called elsewhere)
from ovv.external_services.notion.ops import builders as notion_builders


@dataclass
class CoreResult:
    """
    Stabilizer に返す統合結果（最小）。
    - discord_output: Discord に返す本文
    - notion_ops: Notion へ送る ops（executor が実行）
    - wbs: ThreadWBS（Persist へ保存する前提）
    """
    discord_output: str
    notion_ops: Optional[List[Dict[str, Any]]] = None
    wbs: Optional[Dict[str, Any]] = None


# ------------------------------------------------------------
# Safe helpers
# ------------------------------------------------------------

def _safe_meta_thread_name(packet: InputPacket) -> str:
    """
    thread_name の唯一の入力源は packet.meta.discord_thread_name。
    無い場合は空文字（→ CDC が unconfirmed に収束させる）。
    """
    meta = getattr(packet, "meta", None)
    if isinstance(meta, dict):
        v = meta.get("discord_thread_name")
        if isinstance(v, str):
            return v
    return ""


def _load_wbs(thread_id: str) -> Optional[Dict[str, Any]]:
    try:
        return pg_wbs.load_thread_wbs(thread_id)
    except Exception:
        return None


def _save_wbs(thread_id: str, wbs: Dict[str, Any]) -> None:
    pg_wbs.save_thread_wbs(thread_id, wbs)


# ------------------------------------------------------------
# Core entry
# ------------------------------------------------------------

def handle_packet(packet: InputPacket) -> CoreResult:
    """
    Core の単一エントリポイント。
    Boundary_Gate → Interface_Box から InputPacket を受けて処理する。
    """
    cmd = getattr(packet, "command", None)
    if not isinstance(cmd, str):
        return CoreResult(discord_output="Command missing.")

    if cmd == "task_create":
        return _cmd_task_create(packet)

    if cmd == "wbs_show":
        return _cmd_wbs_show(packet)

    if cmd == "task_pause":
        return _cmd_task_pause(packet)

    if cmd == "task_complete":
        return _cmd_task_complete(packet)

    # WBS accept/edit/finalize 系（あなたのコマンド体系に合わせて）
    if cmd == "wbs_accept":  # !wy
        return _cmd_wbs_accept(packet)

    if cmd == "wbs_edit_accept":  # !we
        return _cmd_wbs_edit_accept(packet)

    if cmd == "wbs_done":  # !wd
        return _cmd_wbs_done(packet)

    if cmd == "wbs_drop":  # !wx
        return _cmd_wbs_drop(packet)

    return CoreResult(discord_output=f"Unknown command: {cmd}")


# ------------------------------------------------------------
# Commands
# ------------------------------------------------------------

def _cmd_task_create(packet: InputPacket) -> CoreResult:
    """
    !t 相当（task_create）
    - ThreadWBS を作成
    - Notion Task を作成（Name は wbs.task）
    """
    thread_id = str(packet.context_key)
    raw_thread_name = _safe_meta_thread_name(packet)

    # 既存があればそれを返す（上書きしない：運用安全）
    existing = _load_wbs(thread_id)
    if isinstance(existing, dict) and existing.get("task"):
        title = str(existing.get("task"))
        return CoreResult(
            discord_output=f"Task already exists: {title}",
            notion_ops=None,
            wbs=existing,
        )

    # CDC は builder 内で実行され、task(title) が確定する
    wbs = wbs_builder.create_empty_wbs(raw_thread_name, trace_id=getattr(packet, "trace_id", None))

    # Persist
    _save_wbs(thread_id, wbs)

    # Notion ops: Name は wbs.task（= CDC title）
    # thread_id は技術プロパティ側へ（builders 側で扱う想定）
    notion_ops = notion_builders.build_task_create_ops(
        *,
        task_name=str(wbs.get("task") or ""),
        thread_id=thread_id,
        context_key=thread_id,
        user_meta=getattr(packet, "user_meta", None) if isinstance(getattr(packet, "user_meta", None), dict) else {},
    )

    return CoreResult(
        discord_output=f"Task created: {wbs.get('task')}",
        notion_ops=notion_ops,
        wbs=wbs,
    )


def _cmd_wbs_show(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(discord_output="WBS not found. Run !t to create.")
    return CoreResult(discord_output=_format_wbs(wbs), wbs=wbs)


def _cmd_task_pause(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(discord_output="WBS not found. Run !t first.")

    wbs = wbs_builder.on_task_pause(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    # Notion summary/duration は Stabilizer 側で処理する想定（本Coreでは ops を作らない）
    return CoreResult(discord_output="Task paused.", wbs=wbs)


def _cmd_task_complete(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(discord_output="WBS not found. Run !t first.")

    wbs = wbs_builder.on_task_complete(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    return CoreResult(discord_output="Task completed.", wbs=wbs)


def _cmd_wbs_accept(packet: InputPacket) -> CoreResult:
    """
    !wy: 直近の CDC candidate を accept する、などは上位で candidate を構築して渡す想定。
    ここでは packet.content を rationale として受ける最小実装（運用上は Interface_Box 側で candidate を構築して渡せ）。
    """
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(discord_output="WBS not found. Run !t first.")

    candidate = {"rationale": str(getattr(packet, "content", "") or "")}
    wbs = wbs_builder.accept_work_item(wbs, candidate, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    return CoreResult(discord_output="Work item accepted.", wbs=wbs)


def _cmd_wbs_edit_accept(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(discord_output="WBS not found. Run !t first.")

    new_rationale = str(getattr(packet, "content", "") or "").strip()
    candidate = {}
    wbs = wbs_builder.edit_and_accept_work_item(
        wbs,
        candidate,
        new_rationale,
        trace_id=getattr(packet, "trace_id", None),
    )
    _save_wbs(thread_id, wbs)

    return CoreResult(discord_output="Work item edited+accepted.", wbs=wbs)


def _cmd_wbs_done(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(discord_output="WBS not found. Run !t first.")

    wbs, finalized = wbs_builder.mark_focus_done(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    if not finalized:
        return CoreResult(discord_output="No focus item to finalize.", wbs=wbs)

    return CoreResult(discord_output="Focus item marked done.", wbs=wbs)


def _cmd_wbs_drop(packet: InputPacket) -> CoreResult:
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(discord_output="WBS not found. Run !t first.")

    reason = str(getattr(packet, "content", "") or "").strip() or None
    wbs, finalized = wbs_builder.mark_focus_dropped(wbs, reason, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    if not finalized:
        return CoreResult(discord_output="No focus item to finalize.", wbs=wbs)

    return CoreResult(discord_output="Focus item dropped.", wbs=wbs)


# ------------------------------------------------------------
# Formatting
# ------------------------------------------------------------

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
            lines.append(f"- {i}: {r} [{st}]".strip())
        else:
            lines.append(f"- {i}: {str(it)}")
    return "```\n" + "\n".join(lines) + "\n```"