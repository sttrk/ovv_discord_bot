# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.4 (InputPacket 対応版)
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional

from ovv.core.ovv_core import run_core
from ovv.external_services.notion.ops.builders import build_notion_ops
from .stabilizer import Stabilizer
from .types import InputPacket


# ============================================================
# Public entry
# ============================================================

async def handle_request(packet: InputPacket) -> str:
    """
    Boundary_Gate から渡された InputPacket を処理し、
    Core → NotionOps Builder → Stabilizer → 最終出力 を組み立てる。
    """

    # --------------------------------------------------------
    # 1. packet から必要要素を抽出（dict は使わない）
    # --------------------------------------------------------
    command_type = packet.command
    raw_text = packet.raw
    arg_text = packet.content

    context_key = packet.context_key
    user_id = packet.author_id

    # task_id = context_key（TEXT）
    task_id = packet.task_id

    # --------------------------------------------------------
    # 2. Core に委譲
    # --------------------------------------------------------
    core_input = {
        "command_type": command_type,
        "raw_text": raw_text,
        "arg_text": arg_text,
        "task_id": task_id,
        "context_key": context_key,
        "user_id": user_id,
    }

    core_output: Dict[str, Any] = run_core(core_input)
    message_for_user: str = core_output.get("message_for_user", "")

    # --------------------------------------------------------
    # 3. NotionOps Builder
    # --------------------------------------------------------
    notion_ops = build_notion_ops(core_output, request=_PacketProxy(packet))

    # --------------------------------------------------------
    # 4. Stabilizer（Persist + NotionOps + Final Message）
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

    final_message = await stabilizer.finalize()
    return final_message


# ============================================================
# Packet Proxy for NotionOps Builder
# ============================================================

class _PacketProxy:
    """
    builders.py 用 proxy
    必要最小限：
        - task_id
        - user_meta
    """

    def __init__(self, packet: InputPacket):
        self.task_id = packet.task_id
        self.user_meta = packet.user_meta

    def __repr__(self):
        return f"<PacketProxy task_id={self.task_id}>"
