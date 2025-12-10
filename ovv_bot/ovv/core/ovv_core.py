# ovv/core/ovv_core.py
# ============================================================
# MODULE CONTRACT: Ovv Core v2.1 (Task Persist / Notion A案 対応版)
#
# ROLE:
#   - BIS / Interface_Box から渡された core_input(dict) を解釈し、
#     「次に Discord ユーザーへ返すメッセージ」と
#     「モード(mode) = タスク操作種別 or free_chat」を決定する。
#
# INPUT (core_input: dict):
#   {
#       "command_type": str,   # "task_create" / "task_start" / "task_paused" / "task_end" / "free_chat"
#       "raw_text": str,       # Discord メッセージ全文
#       "arg_text": str,       # コマンド引数部分（先頭トークンを除いた部分）
#       "task_id": str | None, # context_key と同義（Discord thread_id を TEXT 化）
#       "context_key": str | None,
#       "user_id": str | None,
#   }
#
# OUTPUT (dict):
#   {
#       "message_for_user": str,   # Discord へ返す最終メッセージ本文
#       "mode": str,               # "task_create" / "task_start" / "task_paused" / "task_end" / "free_chat"
#       ...                        # 必要に応じて将来拡張用フィールドを追加
#   }
#
# CONSTRAINTS:
#   - Notion / Persist / Discord API には直接触れない（BIS / Stabilizer 側の責務）。
#   - task_id は TEXT（context_key ベース）として扱い、数値変換しない。
#   - ThreadBrain / RuntimeMemory 等の構造はここでは持たない（将来拡張）。
# ============================================================

from __future__ import annotations

from typing import Any, Dict


# ============================================================
# Public API
# ============================================================


def run_core(core_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    BIS / Interface_Box から呼ばれる唯一のエントリ。

    - command_type に応じて応答メッセージと mode を決定する。
    - Persist / Notion への書き込みは、mode に基づき BIS / Stabilizer が行う。
    """

    command_type: str = core_input.get("command_type", "free_chat")
    raw_text: str = core_input.get("raw_text", "") or ""
    arg_text: str = core_input.get("arg_text", "") or ""
    task_id: str | None = core_input.get("task_id")
    context_key: str | None = core_input.get("context_key")
    user_id: str | None = core_input.get("user_id")

    # safety: task_id は context_key と同義として扱う
    if task_id is None and context_key is not None:
        task_id = str(context_key)

    # --------------------------------------------------------
    # モード別ディスパッチ
    # --------------------------------------------------------
    if command_type == "task_create":
        return _handle_task_create(task_id=task_id, arg_text=arg_text, user_id=user_id)

    if command_type == "task_start":
        return _handle_task_start(task_id=task_id, arg_text=arg_text)

    if command_type == "task_paused":
        return _handle_task_paused(task_id=task_id)

    if command_type == "task_end":
        return _handle_task_end(task_id=task_id)

    # それ以外は free_chat として扱う
    return _handle_free_chat(raw_text=raw_text, user_id=user_id, context_key=context_key)


# ============================================================
# Handlers: Task Operations
# ============================================================


def _handle_task_create(task_id: str | None, arg_text: str, user_id: str | None) -> Dict[str, Any]:
    """
    !t（task_create）に対応。
    - タスクの「名前」を arg_text から決める。
    - Persist/Notion には Stabilizer / NotionOps が書き込む。
    """

    if task_id is None:
        # スレッド外で実行された場合
        msg = (
            "[task_create] このコマンドは Discord スレッド内でのみ使用できます。\n"
            "スレッドを作成した上で再度 `!t <タスク名>` を実行してください。"
        )
        return {
            "message_for_user": msg,
            "mode": "free_chat",  # Persist/Notion には書き込ませない
        }

    title = arg_text.strip() or f"Task {task_id}"
    user_label = user_id or "unknown"

    msg = (
        "[task_create] 新しいタスクを登録しました。\n"
        f"- task_id : {task_id}\n"
        f"- name    : {title}\n"
        f"- created_by : {user_label}"
    )

    return {
        "message_for_user": msg,
        "mode": "task_create",
        "task_name": title,
        "task_id": task_id,
    }


def _handle_task_start(task_id: str | None, arg_text: str) -> Dict[str, Any]:
    """
    !ts（task_start）に対応。
    - Persist 側では task_session.start を記録。
    - Notion 側では status=in_progress / started_at を更新。
    """

    if task_id is None:
        msg = (
            "[task_start] このコマンドは Discord スレッド内でのみ使用できます。\n"
            "スレッドを作成した上で再度 `!ts` を実行してください。"
        )
        return {
            "message_for_user": msg,
            "mode": "free_chat",
        }

    memo = arg_text.strip()
    memo_line = f"- memo   : {memo}\n" if memo else ""

    msg = (
        "[task_start] 学習セッションを開始しました。\n"
        f"- task_id: {task_id}\n"
        f"{memo_line}"
        "※ この時点から task_end までの経過時間が task_session に記録されます。"
    )

    return {
        "message_for_user": msg,
        "mode": "task_start",
        "task_id": task_id,
        "memo": memo,
    }


def _handle_task_paused(task_id: str | None) -> Dict[str, Any]:
    """
    !tp（task_paused）に対応。
    - Persist 側では「イベントログのみ」を残す（session の終了処理はしない）。
    - Notion 側では status=paused に更新。
    """

    if task_id is None:
        msg = (
            "[task_paused] このコマンドは Discord スレッド内でのみ使用できます。\n"
            "スレッドを作成した上で再度 `!tp` を実行してください。"
        )
        return {
            "message_for_user": msg,
            "mode": "free_chat",
        }

    msg = (
        "[task_paused] 学習を一時停止しました。\n"
        f"- task_id: {task_id}\n"
        "※ Persist 上の duration_seconds は、task_end 実行時に計算されます。"
    )

    return {
        "message_for_user": msg,
        "mode": "task_paused",
        "task_id": task_id,
    }


def _handle_task_end(task_id: str | None) -> Dict[str, Any]:
    """
    !tc（task_end/completed）に対応。
    - Persist 側では started_at からの duration_seconds を計算して更新。
    - Stabilizer が取得した duration_seconds を NotionOps に連結し、
      Notion 側の duration_time（または相当プロパティ）に同期可能とする。
    """

    if task_id is None:
        msg = (
            "[task_end] このコマンドは Discord スレッド内でのみ使用できます。\n"
            "スレッドを作成した上で再度 `!tc` を実行してください。"
        )
        return {
            "message_for_user": msg,
            "mode": "free_chat",
        }

    msg = (
        "[task_end] 学習セッションを終了しました。\n"
        f"- task_id: {task_id}\n"
        "※ 実測された duration_seconds は DB に記録され、"
        "後続の NotionOps により TaskDB に同期されます。"
    )

    return {
        "message_for_user": msg,
        "mode": "task_end",
        "task_id": task_id,
    }


# ============================================================
# Handler: Free Chat（暫定版）
# ============================================================


def _handle_free_chat(raw_text: str, user_id: str | None, context_key: str | None) -> Dict[str, Any]:
    """
    free_chat モード。
    現フェーズでは Persist/Notion 実装を優先し、応答は簡易なエコーに留める。
    （LLM 応答統合は後続フェーズで ThreadBrain / 推論コアとともに実装する前提）
    """

    base = raw_text.strip() or "(empty)"

    msg_lines = [
        "[free_chat] 現フェーズではタスク管理機能（Persist v3.0 / Notion 連携）の実装を優先しています。",
        "このメッセージは簡易エコー応答です。",
        "",
        f"- user_id    : {user_id or 'unknown'}",
        f"- context_key: {context_key or 'none'}",
        "",
        "---- Echo ----",
        base,
    ]
    msg = "\n".join(msg_lines)

    return {
        "message_for_user": msg,
        "mode": "free_chat",
    }