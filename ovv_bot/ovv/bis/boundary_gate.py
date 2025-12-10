# ovv/bis/boundary_gate.py
# ---------------------------------------------------------------------
# Boundary Gate Layer
# ・Discord からの入力を受け取り、InputPacket を生成する
# ・コマンド分類（!Task / !Task_s / !Task_e / free_chat）
# ・InterfaceBox への委譲
# ---------------------------------------------------------------------

from ovv.bis.interface_box import handle_request


class InputPacket:
    def __init__(
        self,
        *,
        context_key,
        raw_message,
        command_type,
        payload,
        user_meta,
        task_id=None,
    ):
        # BIS 全体で共有するキー
        self.context_key = context_key

        # Discord の message オブジェクトそのもの
        self.raw_message = raw_message

        # "!Task" などの分類結果
        self.command_type = command_type

        # コマンドに応じた payload
        self.payload = payload

        # user_id / user_name など
        self.user_meta = user_meta

        # Persist v3.0 用 task_id（= Discord thread_id を文字列化）
        self.task_id = task_id


def classify_command(raw_text: str):
    """
    Discord メッセージ文字列から、BIS コマンド種別と payload を判定する。
    """
    raw_text = raw_text.strip()

    if raw_text.startswith("!Task "):
        return "task_create", {"title": raw_text.replace("!Task ", "", 1)}

    if raw_text.startswith("!Task_s"):
        return "task_start", {}

    if raw_text.startswith("!Task_e"):
        return "task_end", {}

    # fallback（通常のチャット）
    return "free_chat", {"text": raw_text}


async def handle_discord_input(message):
    """
    Discord 側から呼び出されるエントリポイント。
    - message: discord.Message
    """

    # コマンド分類用の生テキスト
    raw_text = message.content

    command_type, payload = classify_command(raw_text)

    # context_key / task_id は「1スレッド = 1タスク」を前提に thread_id ベースで作成
    context_key = f"discord-{message.channel.id}"
    task_id = str(message.channel.id)

    user_meta = {
        "user_id": str(message.author.id),
        "user_name": message.author.name,
    }

    packet = InputPacket(
        context_key=context_key,
        raw_message=message,  # ここで message オブジェクトそのものを渡す
        command_type=command_type,
        payload=payload,
        user_meta=user_meta,
        task_id=task_id,
    )

    # InterfaceBox へ引き渡し
    response = await handle_request(packet)

    # 最終出力は Stabilizer が行う
    formatted = await response["stabilizer"].finalize()
    await message.channel.send(formatted)