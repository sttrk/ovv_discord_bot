# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.9 (ThreadWBS + CDC Candidate Runtime Lock)
#
# ROLE:
#   - Boundary_Gate から渡された InputPacket を受け取り、
#     Core → NotionOps Builder → Stabilizer の実行順序を保証する。
#   - ThreadWBS を「推論前コンテキスト」として Core に渡す。
#   - 明示コマンドに限り、ThreadWBS の最小更新（Builder→Persistence）を発火させる。
#   - CDC による work_item 候補は「Runtime のみ」で保持し、!wy/!we/!wn でのみ確定/破棄する。
#
# RESPONSIBILITY TAGS:
#   [ENTRY_IFACE]     handle_request の入口
#   [CTX_BUILD]       推論用コンテキスト構築（ThreadWBS load）
#   [CANDIDATE_STORE] CDC 候補を Runtime に保持（永続化禁止）
#   [WBS_UPDATE]      明示コマンド時のみ Builder→Persistence を発火
#   [DISPATCH]        Core へのディスパッチ
#   [BUILD_OPS]       NotionOps Builder 呼び出し
#   [FINALIZE]        Stabilizer 最終フェーズ接続
#
# CONSTRAINTS (HARD):
#   - LLM による自動改変は禁止（候補の自動採用・自動編集・自動保存は禁止）
#   - CDC 候補は Runtime のみ（PG/Notion/WBS への一時保存禁止）
#   - WBS 更新は「明示コマンド」に限定（!t / !tp / !tc / !wbs / !wy / !wn / !we）
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timezone

from ovv.core.ovv_core import run_core
from ovv.external_services.notion.ops.builders import build_notion_ops
from .stabilizer import Stabilizer
from .types import InputPacket

# ThreadWBS Persistence
from ovv.bis.wbs.thread_wbs_persistence import load_thread_wbs, save_thread_wbs

# ThreadWBS Builder（最小）
from ovv.bis.wbs.thread_wbs_builder import (
    create_empty_wbs,
    propose_work_item,
    accept_work_item,
    reject_work_item,
    edit_and_accept_work_item,
    on_task_pause,
    on_task_complete,
)

# ============================================================
# STEP A: CDC Candidate Runtime Store (HARD)
#   - 1 thread_id = max 1 candidate
#   - reboot で消えてよい（仕様）
# ============================================================

_runtime_cdc_candidates: Dict[str, Dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Internal helpers
# ============================================================

def _select_thread_id(task_id: Any, context_key: Any) -> Optional[str]:
    """
    thread_id は task_id を優先、なければ context_key。
    Discord Ovv 方針: thread_id = task_id = context_key が基本。
    """
    if task_id:
        return str(task_id)
    if context_key is not None:
        return str(context_key)
    return None


def _get_thread_name(packet: InputPacket) -> str:
    """
    !t 時に task 名として使う。
    meta に thread 名があればそれを優先。
    """
    meta = getattr(packet, "meta", None) or {}
    for k in ("discord_thread_name", "discord_channel_name", "thread_name", "channel_name"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return str(getattr(packet, "channel_id", "") or "untitled-task")


def _format_wbs_brief(wbs: Dict[str, Any], max_items: int = 10) -> str:
    """
    デバッグ表示用（参照のみ）。
    """
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
        if isinstance(it, dict):
            rationale = (it.get("rationale") or "").strip()
        if not rationale:
            rationale = "<no rationale>"
        lines.append(f"  - [{i}] {rationale}")

    if len(items) > max_items:
        lines.append(f"  ... ({len(items) - max_items} more)")

    return "\n".join(lines)


# ============================================================
# STEP A: Candidate capture (from Core output)
# ============================================================

def _extract_cdc_rationale(core_output: Dict[str, Any]) -> Optional[str]:
    """
    Core 出力から CDC 候補の rationale を取り出す。
    NOTE:
      - 既存実装との差異を吸収するため、複数キーを順に見る。
      - rationale は 1行のみ（trim）として保持する。
    """
    if not isinstance(core_output, dict):
        return None

    # 想定候補キー（プロジェクト揺れ耐性）
    candidates = [
        core_output.get("cdc_candidate"),
        core_output.get("wbs_candidate"),
        core_output.get("work_item_candidate"),
        core_output.get("candidate"),
    ]

    for c in candidates:
        if isinstance(c, dict):
            r = c.get("rationale")
            if isinstance(r, str) and r.strip():
                return r.strip()

        if isinstance(c, str) and c.strip():
            return c.strip()

    # 直接入っている可能性
    r2 = core_output.get("rationale")
    if isinstance(r2, str) and r2.strip():
        return r2.strip()

    return None


def _store_cdc_candidate_runtime(thread_id: str, rationale: str) -> Tuple[bool, str]:
    """
    STEP A (HARD):
      - 候補は Runtime のみ
      - 既存候補がある場合は上書きしない（勝手な改変防止）
    戻り値:
      - stored: bool（新規保存したか）
      - hint: ユーザー向け短文
    """
    if thread_id in _runtime_cdc_candidates:
        return False, "[CDC] candidate already exists; use !wy / !wn / !we."

    # Builder の propose_work_item 形式を流用（ただし保存は Runtime のみ）
    c = propose_work_item(wbs={}, rationale=rationale)  # wbs 引数は利用しない設計だが互換目的で渡す
    c = {
        "rationale": (c.get("rationale") or "").strip(),
        "generated_at": _now_iso(),
        "source": "cdc",
    }
    _runtime_cdc_candidates[thread_id] = c
    return True, "[CDC] candidate generated. Reply with !wy / !wn / !we."


def _pop_cdc_candidate(thread_id: str) -> Optional[Dict[str, Any]]:
    return _runtime_cdc_candidates.pop(thread_id, None)


def _peek_cdc_candidate(thread_id: str) -> Optional[Dict[str, Any]]:
    return _runtime_cdc_candidates.get(thread_id)


# ============================================================
# WBS Update (explicit commands only)
# ============================================================

def _apply_wbs_update_explicit(
    command_type: Optional[str],
    thread_id: Optional[str],
    packet: InputPacket,
    current_wbs: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    明示コマンド時のみ WBS を更新して保存する（最小）。
    戻り値:
      - updated_wbs
      - user_hint（任意）
    """
    if not command_type or not thread_id:
        return current_wbs, None

    # ---- !t（task_create）: 空WBS生成（存在するなら上書きしない） ----
    if command_type == "task_create":
        if current_wbs is not None:
            return current_wbs, "[WBS] already exists (no overwrite)."
        wbs = create_empty_wbs(_get_thread_name(packet))
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] initialized."

    # ---- !tp（task_paused）: paused ----
    if command_type == "task_paused":
        if current_wbs is None:
            return None, "[WBS] not found; pause skipped."
        wbs = on_task_pause(current_wbs)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] marked as paused."

    # ---- !tc（task_end）: completed ----
    if command_type == "task_end":
        if current_wbs is None:
            return None, "[WBS] not found; complete skipped."
        wbs = on_task_complete(current_wbs)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] marked as completed."

    # ---- !wbs（wbs_show）: 表示のみ（保存なし） ----
    if command_type == "wbs_show":
        if current_wbs is None:
            return None, "[WBS] not found."
        return current_wbs, _format_wbs_brief(current_wbs)

    # ---- !wy（wbs_accept）: Runtime 候補を採用して保存 ----
    if command_type == "wbs_accept":
        if current_wbs is None:
            return None, "[WBS] not found; run !t first."
        cand = _pop_cdc_candidate(thread_id)
        if not cand:
            return current_wbs, "[CDC] no candidate; nothing to accept."
        wbs = accept_work_item(current_wbs, {"rationale": cand["rationale"]})
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] work_item accepted."

    # ---- !wn（wbs_reject）: Runtime 候補を破棄（WBS不変） ----
    if command_type == "wbs_reject":
        cand = _pop_cdc_candidate(thread_id)
        if not cand:
            return current_wbs, "[CDC] no candidate; nothing to reject."
        # WBS は不変（仕様）
        return current_wbs, "[CDC] candidate rejected."

    # ---- !we（wbs_edit）: Runtime 候補を編集採用して保存 ----
    if command_type == "wbs_edit":
        if current_wbs is None:
            return None, "[WBS] not found; run !t first."
        cand = _peek_cdc_candidate(thread_id)
        if not cand:
            return current_wbs, "[CDC] no candidate; nothing to edit."
        edited_text = (packet.content or "").strip()
        if not edited_text:
            return current_wbs, "[CDC] edit text is empty. Usage: !we <new rationale>"
        _pop_cdc_candidate(thread_id)
        wbs = edit_and_accept_work_item(current_wbs, {"rationale": cand["rationale"]}, edited_text)
        save_thread_wbs(thread_id, wbs)
        return wbs, "[WBS] work_item edited & accepted."

    return current_wbs, None


# ============================================================
# [ENTRY_IFACE]
# Public Entry
# ============================================================

async def handle_request(packet: InputPacket) -> str:
    """
    [ENTRY_IFACE]
    BIS パイプライン第二段階。
    """

    # --------------------------------------------------------
    # 1. InputPacket 属性抽出（型安全）
    # --------------------------------------------------------
    command_type = packet.command
    raw_text = packet.raw
    arg_text = packet.content

    context_key = packet.context_key
    task_id = packet.task_id
    user_id = packet.author_id

    # --------------------------------------------------------
    # [CTX_BUILD] ThreadWBS 読み込み（参照）
    # --------------------------------------------------------
    thread_id = _select_thread_id(task_id, context_key)

    thread_wbs: Optional[Dict[str, Any]] = None
    try:
        if thread_id:
            thread_wbs = load_thread_wbs(thread_id)
    except Exception as e:
        print("[Interface_Box:WARN] failed to load ThreadWBS:", repr(e))
        thread_wbs = None

    # --------------------------------------------------------
    # [WBS_UPDATE] 明示コマンド時のみ最小更新（Builder→Persistence）
    # --------------------------------------------------------
    wbs_user_hint: Optional[str] = None
    try:
        thread_wbs, wbs_user_hint = _apply_wbs_update_explicit(
            command_type=command_type,
            thread_id=thread_id,
            packet=packet,
            current_wbs=thread_wbs,
        )
    except Exception as e:
        print("[Interface_Box:WARN] failed to update ThreadWBS:", repr(e))

    # --------------------------------------------------------
    # [DISPATCH] Core 呼び出し（WBSは参照コンテキストとして渡す）
    # --------------------------------------------------------
    core_input: Dict[str, Any] = {
        "command_type": command_type,
        "raw_text": raw_text,
        "arg_text": arg_text,
        "task_id": task_id,
        "context_key": context_key,
        "user_id": user_id,
        "thread_wbs": thread_wbs,  # 参照専用
    }

    core_output = run_core(core_input)

    # --------------------------------------------------------
    # [CANDIDATE_STORE] Core が CDC 候補を返した場合、Runtime にのみ保持
    #   - 上書き禁止（既存候補があるならヒントのみ）
    #   - 永続化しない
    # --------------------------------------------------------
    cdc_hint: Optional[str] = None
    try:
        if thread_id:
            rationale = _extract_cdc_rationale(core_output)
            if rationale:
                stored, hint = _store_cdc_candidate_runtime(thread_id, rationale)
                cdc_hint = hint
    except Exception as e:
        print("[Interface_Box:WARN] failed to store CDC candidate:", repr(e))

    # --------------------------------------------------------
    # IFACE 側の確定ヒントがあれば、ユーザー向け表示に追記（上書き禁止）
    # --------------------------------------------------------
    message_for_user = core_output.get("message_for_user", "") or ""
    hints = [h for h in (wbs_user_hint, cdc_hint) if isinstance(h, str) and h.strip()]
    if hints:
        suffix = "\n\n".join(hints)
        message_for_user = f"{message_for_user}\n\n{suffix}" if message_for_user else suffix

    # --------------------------------------------------------
    # [BUILD_OPS] NotionOps Builder
    # --------------------------------------------------------
    notion_ops = build_notion_ops(core_output, request=_PacketProxy(packet))

    # --------------------------------------------------------
    # [FINALIZE] Stabilizer 呼び出し
    # --------------------------------------------------------
    stabilizer = Stabilizer(
        message_for_user=message_for_user,
        notion_ops=notion_ops,
        context_key=context_key,
        user_id=user_id,
        task_id=task_id,
        command_type=core_output.get("mode"),
        core_output=core_output,
        thread_state={"thread_wbs": thread_wbs} if thread_wbs else None,
    )

    return await stabilizer.finalize()


# ============================================================
# Packet Proxy（Builder 専用）
# ============================================================

class _PacketProxy:
    """
    NotionOps Builder が要求する最小 API を提供。
    """

    def __init__(self, packet: InputPacket):
        self.task_id = packet.task_id
        self.user_meta = packet.user_meta
        self.context_key = packet.context_key
        self.meta = packet.meta

    def __repr__(self) -> str:
        return f"<PacketProxy task_id={self.task_id}>"