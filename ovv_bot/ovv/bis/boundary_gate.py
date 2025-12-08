# ovv/bis/boundary_gate.py
# ---------------------------------------------------------------------
# Boundary Gate Layer
# ・InputPacket の正式生成
# ・コマンド分類
# ・InterfaceBox への委譲
# ---------------------------------------------------------------------

from ovv.bis.interface_box import handle_request


class InputPacket:
    def __init__(self, *, context_key, raw_message, command_type, payload, user_meta):
        self.context_key = context_key
        self.raw_message = raw_message
        self.command_type = command_type
        self.payload = payload
        self.user_meta = user_meta


def classify_command(raw: str):
    raw = raw.strip()

    if raw.startswith("!Task "):
        return "task_create", {"title": raw.replace("!Task ", "", 1)}

    if raw.startswith("!Task_s"):
        return "task_start", {}

    if raw.startswith("!Task_e"):
        return "task_end", {}

    # fallback（通常のチャット）
    return "free_chat", {"text": raw}


async def handle_discord_input(message):
    raw = message.content

    command_type, payload = classify_command(raw)

    context_key = f"discord-{message.channel.id}"

    user_meta = {
        "user_id": str(message.author.id),
        "user_name": message.author.name,
    }

    packet = InputPacket(
        context_key=context_key,
        raw_message=raw,
        command_type=command_type,
        payload=payload,
        user_meta=user_meta,
    )

    response = await handle_request(packet)

    # 最終出力は stabilizer が行う
    formatted = await response["stabilizer"].finalize()
    await message.channel.send(formatted)