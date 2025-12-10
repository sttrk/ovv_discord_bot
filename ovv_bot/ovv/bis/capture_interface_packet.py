# ============================================================
# capture_interface_packet.py
# Minimal Task Command Edition (t / ts / tp / tc)
# BIS v3.x / Persist v3.0 対応
# ============================================================

import re
from .capture_interface_packet import InputPacket  # 同ファイル内なら不要

# ------------------------------------------------------------
# コマンドマッピング
# ------------------------------------------------------------

COMMAND_MAP = {
    "!t":  "task_create",
    "!ts": "task_start",
    "!tp": "task_pause",      # 実質 task_end として扱う
    "!tc": "task_completed",  # 実質 task_end として扱う
}


def detect_command(message_content: str):
    """
    "!t" "!ts" "!tp" "!tc" を検出して command_type を返す。
    None → free_chat として処理される。
    """
    msg = message_content.strip().lower()

    if msg in COMMAND_MAP:
        return COMMAND_MAP[msg]

    return None


# ------------------------------------------------------------
# Main function: Discord Message → InputPacket
# ------------------------------------------------------------

def capture_interface_packet(discord_message) -> InputPacket:
    """
    Discord メッセージから InputPacket を構築する入口。
    Boundary_Gate からのみ呼ばれる。
    """

    # メッセージ本文
    user_input = discord_message.content

    # Discord thread_id（None のケースもある）
    context_key = (
        str(discord_message.channel.id)
        if hasattr(discord_message, "channel") else None
    )

    # task_id は context_key と同一
    task_id = context_key

    # User 情報
    user_id = str(discord_message.author.id)
    user_name = str(discord_message.author.display_name)

    # コマンド判定
    command_type = detect_command(user_input)

    # Pause / Completed は task_end と同じ Persist オペレーションになる
    if command_type in ("task_pause", "task_completed"):
        normalized_command = "task_end"
    else:
        normalized_command = command_type

    packet = InputPacket(
        user_input=user_input,
        context_key=context_key,
        user_id=user_id,
        user_name=user_name,
        command_type=normalized_command,
        task_id=task_id,
        raw_message=discord_message,
    )

    return packet