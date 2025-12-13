# ovv/core/ovv_core.py
# ============================================================
# MODULE CONTRACT: CORE / OvvCore v1.4
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
#   - Notion Task 名は必ず CDC 済み title（wbs["task"]）を用いる
#   - NotionOps Builder は core_output["mode"] と core_output["task_title"] を参照する
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List

from ovv.bis.types import InputPacket
from ovv.bis.wbs import thread_wbs_builder as wbs_builder

# Persist adapters
from database import pg_wbs

# Notion ops builder (single public entry)
from ovv.external_services.notion.ops.builders import build_notion_ops


@dataclass
class CoreResult:
    """
    Stabilizer に返す統合結果（最小）。

    Fields:
      - discord_output: Discord に返す本文
      - notion_ops: Notion へ送る ops（executor が実行）
      - wbs: ThreadWBS（Persist へ保存する前提）
      - core_output: NotionOps Builder の入力（mode/task_title を含む）
    """
    discord_output: str
    notion_ops: Optional[List[Dict[str, Any]]] = None
    wbs: Optional[Dict[str, Any]] = None
    core_output: Optional[Dict[str, Any]] = None


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
    """
    Persist / ThreadWBS のロード。

    NOTE:
      - database.pg_wbs の公開APIは get_wbs/save_wbs/delete_wbs
        （load_thread_wbs 等ではない）
    """
    try:
        return pg_wbs.get_wbs(thread_id)
    except Exception:
        return None


def _save_wbs(thread_id: str, wbs: Dict[str, Any]) -> None:
    """
    Persist / ThreadWBS の保存。
    """
    try:
        pg_wbs.save_wbs(thread_id, wbs)
    except Exception:
        # Persist 失敗は上位の Stabilizer/Boundary の failsafe に委ねる
        # （Core はここで例外を握りつぶすかどうかは方針次第だが、
        #  現行は Boundary で集約する設計のため、ここでは落とさない）
        raise


def _mk_core_output(
    *,
    mode: str,
    task_title: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    NotionOps Builder が参照する最小 core_output を確定する。

    CONTRACT:
      - mode: 必須
      - task_title: task_create/task_paused/task_end 等で利用
    """
    out: Dict[str, Any] = {"mode": mode}
    if task_title is not None:
        out["task_title"] = task_title
    if isinstance(extra, dict) and extra:
        out.update(extra)
    return out


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
        return CoreResult(discord_output="Command missing.", notion_ops=[], core_output=_mk_core_output(mode="free_chat"))

    if cmd == "task_create":
        return _cmd_task_create(packet)

    if cmd == "wbs_show":
        return _cmd_wbs_show(packet)

    if cmd == "task_paused":  # BoundaryGate mapping: !tp -> task_paused
        return _cmd_task_pause(packet)

    if cmd == "task_end":     # BoundaryGate mapping: !tc -> task_end
        return _cmd_task_complete(packet)

    if cmd == "wbs_accept":       # !wy
        return _cmd_wbs_accept(packet)

    if cmd == "wbs_edit":         # !we (BoundaryGate mapping)
        return _cmd_wbs_edit_accept(packet)

    if cmd == "wbs_done":         # !wd
        return _cmd_wbs_done(packet)

    if cmd == "wbs_drop":         # !wx
        return _cmd_wbs_drop(packet)

    # task_start は現仕様上 Core 実装対象ならここに追加（現状は未実装）
    # if cmd == "task_start": return _cmd_task_start(packet)

    return CoreResult(discord_output=f"Unknown command: {cmd}", notion_ops=[], core_output=_mk_core_output(mode="free_chat"))


# ------------------------------------------------------------
# Commands
# ------------------------------------------------------------

def _cmd_task_create(packet: InputPacket) -> CoreResult:
    """
    !t 相当（task_create）
    - ThreadWBS を作成
    - Notion Task を作成（Name は CDC 済み title = wbs["task"]）
    """
    thread_id = str(packet.context_key)
    raw_thread_name = _safe_meta_thread_name(packet)
    trace_id = getattr(packet, "trace_id", None)

    # 既存があればそれを返す（上書きしない：運用安全）
    existing = _load_wbs(thread_id)
    if isinstance(existing, dict) and existing.get("task"):
        title = str(existing.get("task") or "")
        core_output = _mk_core_output(mode="task_create", task_title=title)
        # 既存タスクの場合は Notion create を発行しない（重複作成防止）
        return CoreResult(
            discord_output=f"Task already exists: {title}",
            notion_ops=[],
            wbs=existing,
            core_output=core_output,
        )

    # CDC は builder 内で実行され、task(title) が確定する
    wbs = wbs_builder.create_empty_wbs(raw_thread_name, trace_id=trace_id)
    title = str(wbs.get("task") or "")

    # Persist
    _save_wbs(thread_id, wbs)

    # CoreOutput → NotionOps（Builder で生成）
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
    """
    !tp
    - WBS 状態更新のみ（詳細 summary/duration は Stabilizer が augment）
    - NotionOps は mode=task_paused を通知
    """
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(
            discord_output="WBS not found. Run !t first.",
            notion_ops=[],
            core_output=_mk_core_output(mode="free_chat"),
        )

    wbs = wbs_builder.on_task_pause(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    title = str(wbs.get("task") or "")
    core_output = _mk_core_output(mode="task_paused", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult(
        discord_output="Task paused.",
        notion_ops=notion_ops,
        wbs=wbs,
        core_output=core_output,
    )


def _cmd_task_complete(packet: InputPacket) -> CoreResult:
    """
    !tc
    - WBS 状態更新のみ（summary/duration は Stabilizer が augment）
    - NotionOps は mode=task_end を通知
    """
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(
            discord_output="WBS not found. Run !t first.",
            notion_ops=[],
            core_output=_mk_core_output(mode="free_chat"),
        )

    wbs = wbs_builder.on_task_complete(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    title = str(wbs.get("task") or "")
    core_output = _mk_core_output(mode="task_end", task_title=title)
    notion_ops = build_notion_ops(core_output, packet)

    return CoreResult(
        discord_output="Task completed.",
        notion_ops=notion_ops,
        wbs=wbs,
        core_output=core_output,
    )


def _cmd_wbs_accept(packet: InputPacket) -> CoreResult:
    """
    !wy
    - 仕様：ユーザー明示 accept のみが work_item を確定し得る
    - candidate は Interface_Box 側で構築するのが本筋だが、最小として content を rationale に入れる
    """
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(
            discord_output="WBS not found. Run !t first.",
            notion_ops=[],
            core_output=_mk_core_output(mode="free_chat"),
        )

    candidate = {"rationale": str(getattr(packet, "content", "") or "")}
    wbs = wbs_builder.accept_work_item(wbs, candidate, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    return CoreResult(
        discord_output="Work item accepted.",
        notion_ops=[],
        wbs=wbs,
        core_output=_mk_core_output(mode="free_chat"),
    )


def _cmd_wbs_edit_accept(packet: InputPacket) -> CoreResult:
    """
    !we
    """
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(
            discord_output="WBS not found. Run !t first.",
            notion_ops=[],
            core_output=_mk_core_output(mode="free_chat"),
        )

    new_rationale = str(getattr(packet, "content", "") or "").strip()
    candidate: Dict[str, Any] = {}
    wbs = wbs_builder.edit_and_accept_work_item(
        wbs,
        candidate,
        new_rationale,
        trace_id=getattr(packet, "trace_id", None),
    )
    _save_wbs(thread_id, wbs)

    return CoreResult(
        discord_output="Work item edited+accepted.",
        notion_ops=[],
        wbs=wbs,
        core_output=_mk_core_output(mode="free_chat"),
    )


def _cmd_wbs_done(packet: InputPacket) -> CoreResult:
    """
    !wd
    """
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(
            discord_output="WBS not found. Run !t first.",
            notion_ops=[],
            core_output=_mk_core_output(mode="free_chat"),
        )

    wbs, finalized = wbs_builder.mark_focus_done(wbs, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    if not finalized:
        return CoreResult(
            discord_output="No focus item to finalize.",
            notion_ops=[],
            wbs=wbs,
            core_output=_mk_core_output(mode="free_chat"),
        )

    # finalized_item の Notion 反映は Stabilizer の責務（Summary Spec v1.1）
    # Stabilizer が thread_state["finalized_item"] を参照するため、ここで渡す
    core_output = _mk_core_output(mode="free_chat")
    return CoreResult(
        discord_output="Focus item marked done.",
        notion_ops=[],
        wbs=wbs,
        core_output=core_output,
    )


def _cmd_wbs_drop(packet: InputPacket) -> CoreResult:
    """
    !wx
    """
    thread_id = str(packet.context_key)
    wbs = _load_wbs(thread_id)
    if not wbs:
        return CoreResult(
            discord_output="WBS not found. Run !t first.",
            notion_ops=[],
            core_output=_mk_core_output(mode="free_chat"),
        )

    reason = str(getattr(packet, "content", "") or "").strip() or None
    wbs, finalized = wbs_builder.mark_focus_dropped(wbs, reason, trace_id=getattr(packet, "trace_id", None))
    _save_wbs(thread_id, wbs)

    if not finalized:
        return CoreResult(
            discord_output="No focus item to finalize.",
            notion_ops=[],
            wbs=wbs,
            core_output=_mk_core_output(mode="free_chat"),
        )

    core_output = _mk_core_output(mode="free_chat")
    return CoreResult(
        discord_output="Focus item dropped.",
        notion_ops=[],
        wbs=wbs,
        core_output=core_output,
    )


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
            label = f"- {i}: {r}"
            if st:
                label += f" [{st}]"
            lines.append(label)
        else:
            lines.append(f"- {i}: {str(it)}")
    return "```\n" + "\n".join(lines) + "\n```"