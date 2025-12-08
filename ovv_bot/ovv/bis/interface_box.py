# ovv/bis/interface_box.py
# ---------------------------------------------------------------------
# Interface Box Layer
# ・InputPacket → Request
# ・Core I/O（system/user prompt の整流）
# ・Core 実行
# ・NotionOps（builders）生成
# ・Response 構築
# ---------------------------------------------------------------------

from ovv.core.ovv_core import run_ovv_core
from external_services.notion.ops.builders import build_notion_ops
from ovv.bis.stabilizer import Stabilizer


class Request:
    def __init__(self, *, context_key, command_type, payload, user_meta):
        self.context_key = context_key
        self.command_type = command_type
        self.payload = payload
        self.user_meta = user_meta


def convert_to_request(packet):
    return Request(
        context_key=packet.context_key,
        command_type=packet.command_type,
        payload=packet.payload,
        user_meta=packet.user_meta,
    )


def build_system_prompt(request: Request) -> str:
    return f"""
You are Ovv — a universal product engineer.
Command Type: {request.command_type}
User: {request.user_meta.get('user_name')}
"""


def build_user_prompt(request: Request) -> str:
    return f"""
User Input:
{request.payload}
"""


async def handle_request(packet):
    request = convert_to_request(packet)

    system_prompt = build_system_prompt(request)
    user_prompt = build_user_prompt(request)

    core_output = await run_ovv_core(system_prompt, user_prompt)

    # notion_ops の生成
    notion_ops = build_notion_ops(core_output, request)

    # Stabilizer に渡すための統合レスポンス
    stabilizer = Stabilizer(
        message_for_user=core_output.get("reply", ""),
        notion_ops=notion_ops,
        context_key=request.context_key,
        user_id=request.user_meta.get("user_id"),
    )

    return {
        "stabilizer": stabilizer,
    }