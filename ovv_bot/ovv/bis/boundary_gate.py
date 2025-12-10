# ============================================================
# MODULE CONTRACT: BIS / Boundary Gate
# LAYER: BIS-1 (Boundary_Gate)
#
# ROLE:
#   - Discord 生 I/O を受け取り InputPacket を生成する唯一の入口層
#   - コマンド分類（Command Classification）
#   - task_id = thread_id(channel.id) の付与（Persist v3.0 要件）
#   - Interface_Box に処理を委譲
#
# MUST:
#   - Discord 生オブジェクトはこの層で完結させる
#   - Core / Persist / External Services / Stabilizer のロジックを混在させない
#
# RESPONSIBILITY TAG:
#   BIS-BOUNDARY-GATE
# ============================================================

from ovv.bis.interface_box import handle_request


# ------------------------------------------------------------
# InputPacket（Boundary → InterfaceBox の専用コンテナ）
# ------------------------------------------------------------
class InputPacket:
    """
    RESPONSIBILITY TAG: BIS-BOUNDARY-GATE-INPUTPACKET
    Boundary_Gate → Interface_Box 専用の入力データ構造。

    ATTRIBUTES:
        context_key : str
        raw_message : discord.Message
        command_type: str
        payload     : dict
        user_meta   : dict
        task_id     : str | None
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


# ------------------------------------------------------------
# Command Classification
# ------------------------------------------------------------
def classify_command(raw: str):
    """
    Discord メッセージ文字列から Ovv コマンド種別を判定する。
    """
    raw = raw.strip()

    if raw.startswith("!Task "):
        return "task_create", {"title": raw.replace("!Task ", "", 1)}

    if raw.startswith("!Task_s"):
        return "task_start", {}

    if raw.startswith("!Task_e"):
        return "task_end", {}

    # fallback（通常チャット）
    return "free_chat", {"text": raw}


# ------------------------------------------------------------
# 軽量エラー応答（Boundary 層のみで使う）
# ------------------------------------------------------------
async def safe_reply(message, text: str):
    try:
        await message.channel.send(text)
    except Exception:
        pass


# ------------------------------------------------------------
# Main Entry: handle_discord_input
# ------------------------------------------------------------
async def handle_discord_input(message):
    """
    Discord on_message → BoundaryGate のエントリポイント。

    RESPONSIBILITY:
        - Discord 生オブジェクトから安全に InputPacket を構築
        - task_id = channel.id（1タスク = 1スレッド）を付与
        - Interface_Box に委譲し、Stabilizer.finalize の結果を送信
    """

    # --------------------------------------------------------
    # Input Firewall（Boundary 版）
    # --------------------------------------------------------
    if message.author.bot:
        return

    raw = message.content or ""
    if raw.strip() == "":
        return  # 空文字は無視

    # --------------------------------------------------------
    # コマンド種別判定
    # --------------------------------------------------------
    command_type, payload = classify_command(raw)

    # context_key（従来仕様）— channel.id ベース
    channel_id = getattr(message.channel, "id", None)
    context_key = f"discord-{channel_id}"

    # --------------------------------------------------------
    # Persist v3.0：task_id = thread_id = channel.id
    # --------------------------------------------------------
    task_id = str(channel_id) if channel_id is not None else None

    # --------------------------------------------------------
    # User meta
    # --------------------------------------------------------
    user_meta = {
        "user_id": str(message.author.id),
        "user_name": getattr(message.author, "name", None)
        or getattr(message.author, "display_name", None)
        or str(message.author.id),
    }

    # --------------------------------------------------------
    # InputPacket 生成
    # --------------------------------------------------------
    packet = InputPacket(
        context_key=context_key,
        raw_message=message,
        command_type=command_type,
        payload=payload,
        user_meta=user_meta,
        task_id=task_id,
    )

    # --------------------------------------------------------
    # Interface_Box に委譲
    # --------------------------------------------------------
    try:
        response = await handle_request(packet)
    except Exception as exc:
        await safe_reply(message, f"[Boundary Error] {exc}")
        return

    # --------------------------------------------------------
    # Stabilizer が安定化したメッセージを Discord に送信
    # --------------------------------------------------------
    try:
        formatted = await response["stabilizer"].finalize()
        await message.channel.send(formatted)
    except Exception as exc:
        await safe_reply(message, f"[Stabilizer Error] {exc}")
        return