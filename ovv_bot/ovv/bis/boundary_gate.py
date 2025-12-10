# ovv/bis/boundary_gate.py
# ============================================================
# MODULE CONTRACT: BIS / Boundary_Gate v3.1
#
# ROLE:
#   - Discord からの生メッセージを受け取り、
#     「どの種別のリクエストか」を判定して Interface_Box に渡す。
#   - Discord API / Core / Persist / Notion に直接触れない。
#
# INPUT (from bot.py):
#   - discord.Message 相当の message オブジェクト
#
# OUTPUT (to bot.py):
#   - str | None
#       Discord に送信するべきメッセージ本文。
#       None の場合は「何もしない」。
#
# INTERNAL ENVELOPE (to Interface_Box):
#   envelope: dict = {
#       "command_type": str,   # "task_create" / "task_start" / "task_paused" / "task_end" / "free_chat"
#       "raw_text": str,       # メッセージ全文
#       "arg_text": str,       # コマンドを除いた引数部（例: "!t xxx" → "xxx"）
#       "context_key": str,    # Discord thread_id or channel_id（TEXT）
#       "user_id": str | None, # Discord user id（TEXT）
#   }
#
# CONSTRAINTS:
#   - Discord への送信は呼び出し元（bot.py）が行う。
#   - Core / Persist / Notion への import は禁止。
#   - BIS 内の他層では Interface_Box のみ参照可能。
#   - task_id = context_key = Discord thread_id (TEXT) を前提とする。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional

from .interface_box import handle_request


# ============================================================
# Public Entry
# ============================================================

async def handle_discord_input(message: Any) -> Optional[str]:
    """
    Discord 側から直接呼び出される入口関数。

    Parameters
    ----------
    message : discord.Message 互換オブジェクト

    Returns
    -------
    response_text : str | None
        Discord に送信するメッセージ本文。
        None の場合はレスポンス不要。
    """

    # --------------------------------------------------------
    # 1. Bot 自身 or 空メッセージは無視
    # --------------------------------------------------------
    author = getattr(message, "author", None)
    if author is not None and getattr(author, "bot", False):
        return None

    content = (getattr(message, "content", "") or "").strip()
    if not content:
        return None

    # --------------------------------------------------------
    # 2. コマンド解析
    #    - !t   → task_create
    #    - !ts  → task_start
    #    - !tp  → task_paused
    #    - !tc  → task_end
    #    ※ 旧コマンドも後方互換として受け付ける。
    # --------------------------------------------------------
    command, arg_text = _split_command(content)
    command_type = _map_command_type(command)

    # --------------------------------------------------------
    # 3. コンテキストキー / ユーザーID 抽出
    #    - 原則: thread_id を優先
    #    - なければ channel_id
    # --------------------------------------------------------
    context_key = _extract_context_key(message)
    user_id = _extract_user_id(message)

    # context_key が取れないケース（特殊な DM 等）は Persist 対象外として free_chat
    if context_key is None:
        context_key = "no_context"

    # --------------------------------------------------------
    # 4. Envelope を構築し、Interface_Box に委譲
    # --------------------------------------------------------
    envelope: Dict[str, Any] = {
        "command_type": command_type,
        "raw_text": content,
        "arg_text": arg_text,
        "context_key": context_key,
        "user_id": user_id,
    }

    response_text = await handle_request(envelope)
    return response_text


# ============================================================
# Helpers: Command Parsing
# ============================================================

def _split_command(content: str) -> tuple[str, str]:
    """
    先頭トークンをコマンドとして分離する。

    Returns
    -------
    command : str
        先頭トークン（例: "!t", "!ts"）。先頭が "!" でなければ ""。
    arg_text : str
        コマンドを除いた残り全文。コマンドでない場合は content 全体。
    """
    if not content.startswith("!"):
        return "", content

    parts = content.split(maxsplit=1)
    command = parts[0].lower()
    arg_text = parts[1] if len(parts) > 1 else ""
    return command, arg_text


def _map_command_type(command: str) -> str:
    """
    Discord コマンド文字列を内部 command_type にマッピングする。

    Mapping
    -------
    "!t" / "!task"                → "task_create"
    "!ts" / "!task_s" / "!task_start"
                                  → "task_start"
    "!tp" / "!task_p" / "!task_pause" / "!task_paused"
                                  → "task_paused"
    "!tc" / "!te" / "!task_c" / "!task_e" / "!task_end" / "!task_complete" / "!task_completed"
                                  → "task_end"
    その他                         → "free_chat"
    """
    # task_create
    if command in ("!t", "!task"):
        return "task_create"

    # task_start
    if command in ("!ts", "!task_s", "!task_start"):
        return "task_start"

    # task_paused
    if command in ("!tp", "!task_p", "!task_pause", "!task_paused"):
        return "task_paused"

    # task_end / completed
    if command in ("!tc", "!te", "!task_c", "!task_e", "!task_end", "!task_complete", "!task_completed"):
        return "task_end"

    # それ以外は通常会話として扱う
    return "free_chat"


# ============================================================
# Helpers: Context / User extraction
# ============================================================

def _extract_context_key(message: Any) -> Optional[str]:
    """
    Discord スレッド / チャンネルから context_key を抽出する。

    優先順:
      1. message.thread.id
      2. message.channel.id
    """
    # Thread 優先
    thread = getattr(message, "thread", None)
    if thread is not None and hasattr(thread, "id"):
        try:
            return str(thread.id)
        except Exception:
            pass

    # Channel fallback
    channel = getattr(message, "channel", None)
    if channel is not None and hasattr(channel, "id"):
        try:
            return str(channel.id)
        except Exception:
            pass

    return None


def _extract_user_id(message: Any) -> Optional[str]:
    """
    Discord メッセージから user_id(TEXT) を抽出する。
    """
    author = getattr(message, "author", None)
    if author is None:
        return None

    if hasattr(author, "id"):
        try:
            return str(author.id)
        except Exception:
            return None

    return None