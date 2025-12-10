# ovv/bis/boundary_gate.py
# ---------------------------------------------------------------------
# Boundary Gate Layer
# ・Discord 生 I/O を受け取り InputPacket を生成
# ・コマンド分類
# ・InterfaceBox への委譲
#
# NOTE:
#   - 1タスク = 1スレッド という Persist v3.0 の前提に合わせ、
#     task_id として Discord の channel.id（= スレッドID を含む）を採用する。
# ---------------------------------------------------------------------

from ovv.bis.interface_box import handle_request


class InputPacket:
    """
    RESPONSIBILITY TAG: BIS-BOUNDARY-GATE-INPUTPACKET

    Boundary_Gate → Interface_Box 間でのみ使用される入力コンテナ。

    ATTRIBUTES:
        context_key : str
        raw_message : discord.Message
        command_type: str
        payload     : dict
        user_meta   : dict
        task_id     : str | None   # Persist v3.0 用（thread_id ベース）
    """

    def __init__(
        self,
        *,
        context_key: str,
        raw_message,
        command_type: str,
        payload: dict,
        user_meta: dict,
        task_id: str | None = None,
    ):
        self.context_key = context_key
        self.raw_message = raw_message
        self.command_type = command_type
        self.payload = payload
        self.user_meta = user_meta
        self.task_id = task_id


def classify_command(raw: str):
    """
    Discord メッセージ文字列から Ovv コマンド種別を判定する。
    将来的にコマンドが増える場合はここに集約する。
    """
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
    """
    Discord on_message から直接呼び出されるエントリポイント。

    RESPONSIBILITY:
        - Discord.Message から InputPacket を組み立てる
        - task_id = thread_id（実体としては channel.id）を付与する
        - Interface_Box に処理を委譲する
        - Stabilizer が整形したメッセージを Discord に送信する
    """

    # 元のメッセージ文字列
    raw = message.content

    # コマンド種別判定
    command_type, payload = classify_command(raw)

    # context_key は従来どおり channel.id ベース
    context_key = f"discord-{message.channel.id}"

    # 1タスク = 1スレッド:
    # task_id として channel.id（スレッドID を含む）をそのまま文字列化して使う。
    channel_id = getattr(message.channel, "id", None)
    task_id = str(channel_id) if channel_id is not None else None

    # ユーザメタ
    user_meta = {
        "user_id": str(message.author.id),
        "user_name": getattr(message.author, "name", None)
        or getattr(message.author, "display_name", None)
        or str(message.author.id),
    }

    # InputPacket を生成（raw_message は Discord.Message オブジェクト）
    packet = InputPacket(
        context_key=context_key,
        raw_message=message,
        command_type=command_type,
        payload=payload,
        user_meta=user_meta,
        task_id=task_id,
    )

    # Interface_Box に処理委譲
    response = await handle_request(packet)

    # 最終出力は Stabilizer が行う
    formatted = await response["stabilizer"].finalize()
    await message.channel.send(formatted)