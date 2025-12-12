# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.8 (ThreadWBS Minimal Update Hook)
#
# ROLE:
#   - Boundary_Gate から渡された InputPacket を受け取り、
#     Core → NotionOps Builder → Stabilizer の実行順序を保証する。
#   - ThreadWBS を「推論前コンテキスト」として Core に渡す。
#   - ただし、明示コマンド（!t / !tp / !tc / !wbs）に限り、
#     ThreadWBS の最小更新（Builder→Persistence）を IFACE 側で発火させる。
#   - PacketCapture / DebugLayer と構造整合性を保つ。
#
# RESPONSIBILITY TAGS:
#   [ENTRY_IFACE]  handle_request の入口
#   [CTX_BUILD]    推論用コンテキスト構築（ThreadWBS load）
#   [WBS_UPDATE]   明示コマンド時のみ Builder→Persistence を発火（最小）
#   [DISPATCH]     Core へのディスパッチ
#   [BUILD_OPS]    NotionOps Builder 呼び出し
#   [FINALIZE]     Stabilizer 最終フェーズ接続
#
# CONSTRAINTS:
#   - LLM による自動改変は禁止（ここでは候補生成も行わない）
#   - WBS の更新は「明示コマンド」に限定
#   - WBS の構造解釈は最小限（表示整形のみ）
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ovv.core.ovv_core import run_core
from ovv.external_services.notion.ops.builders import build_notion_ops
from .stabilizer import Stabilizer
from .types import InputPacket

# ThreadWBS Persistence
from ovv.bis.wbs.thread_wbs_persistence import load_thread_wbs, save_thread_wbs

# ThreadWBS Builder（最小）
from ovv.bis.wbs.thread_wbs_builder import (
    create_empty_wbs,
    on_task_pause,
    on_task_complete,
)


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
    - 可能なら meta からスレッド名/チャンネル名を拾う（あれば）。
    - なければ channel_id をフォールバックにする（安定優先）。
    """
    meta = getattr(packet, "meta", None) or {}
    for k in ("discord_thread_name", "discord_channel_name", "thread_name", "channel_name"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # 最低限の安定フォールバック
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


def _apply_minimal_wbs_update(
    command_type: Optional[str],
    thread_id: Optional[str],
    packet: InputPacket,
    current_wbs: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    明示コマンド時のみ WBS を更新して保存する（最小）。
    戻り値:
      - updated_wbs: 更新後のWBS（未更新なら current_wbs をそのまま返す/None）
      - user_hint: IFACE で確定できるユーザー向け短文（任意）
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
            # 最小実装では暗黙生成は避ける（意図しない状態生成を防ぐ）
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

    # ---- 将来: !wbs（wbs_show）など（表示のみ、保存なし） ----
    if command_type == "wbs_show":
        if current_wbs is None:
            return None, "[WBS] not found."
        return current_wbs, _format_wbs_brief(current_wbs)

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
    #   - LLM による候補生成や改変は禁止（ここでは行わない）
    # --------------------------------------------------------
    wbs_user_hint: Optional[str] = None
    try:
        thread_wbs, wbs_user_hint = _apply_minimal_wbs_update(
            command_type=command_type,
            thread_id=thread_id,
            packet=packet,
            current_wbs=thread_wbs,
        )
    except Exception as e:
        # WBS 更新失敗は致命扱いにしない（推論継続）
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
        "thread_wbs": thread_wbs,  # 推論用コンテキスト（参照専用）
    }

    core_output = run_core(core_input)

    # IFACE 側で確定したヒントがあれば、ユーザー向け表示に合成（上書きではなく追記）
    message_for_user = core_output.get("message_for_user", "") or ""
    if wbs_user_hint:
        if message_for_user:
            message_for_user = f"{message_for_user}\n\n{wbs_user_hint}"
        else:
            message_for_user = wbs_user_hint

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