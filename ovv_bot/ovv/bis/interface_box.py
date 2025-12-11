# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.6 (Responsibility Complete)
#
# ROLE:
#   - Boundary_Gate から渡された InputPacket を受け取り、
#     Core → NotionOps Builder → Stabilizer の実行順序を保証する。
#   - PacketCapture / DebugLayer と構造整合性を保つ。
#
# RESPONSIBILITY TAGS:
#   [ENTRY_IFACE]  handle_request の入口
#   [DISPATCH]     Core へのディスパッチ
#   [BUILD_OPS]    NotionOps Builder 呼び出し
#   [FINALIZE]     Stabilizer 最終フェーズ接続
# ============================================================

from __future__ import annotations
from typing import Any, Dict

from ovv.core.ovv_core import run_core
from ovv.external_services.notion.ops.builders import build_notion_ops
from .stabilizer import Stabilizer
from .types import InputPacket


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
    # [DISPATCH] Core 呼び出し
    # --------------------------------------------------------
    core_input = {
        "command_type": command_type,
        "raw_text": raw_text,
        "arg_text": arg_text,
        "task_id": task_id,
        "context_key": context_key,
        "user_id": user_id,
    }

    core_output = run_core(core_input)
    message_for_user = core_output.get("message_for_user", "")

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
        thread_state=None,
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

    def __repr__(self):
        return f"<PacketProxy task_id={self.task_id}>"