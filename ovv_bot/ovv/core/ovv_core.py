# ovv/core/ovv_core.py
# ============================================================
# Ovv Core v2.1 — Task A案完全対応（name / duration / status）
# ============================================================

from __future__ import annotations
from typing import Any, Dict


def run_core(core_input: Dict[str, Any]) -> Dict[str, Any]:
    command_type = core_input.get("command_type", "free_chat")
    raw_text = core_input.get("raw_text", "") or ""
    arg_text = core_input.get("arg_text", "") or ""
    task_id = core_input.get("task_id")
    context_key = core_input.get("context_key")
    user_id = core_input.get("user_id")

    if task_id is None and context_key is not None:
        task_id = str(context_key)

    if command_type == "task_create":
        return _handle_task_create(task_id, arg_text, user_id)

    if command_type == "task_start":
        return _handle_task_start(task_id, arg_text)

    if command_type == "task_paused":
        return _handle_task_paused(task_id)

    if command_type == "task_end":
        return _handle_task_end(task_id)

    return _handle_free_chat(raw_text, user_id, context_key)


# ============================================================
# Handlers
# ============================================================

def _handle_task_create(task_id: str | None, arg_text: str, user_id: str | None):
    if task_id is None:
        return {
            "message_for_user": (
                "[task_create] このコマンドはスレッド内でのみ有効です。"
            ),
            "mode": "free_chat",
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


def _handle_task_start(task_id: str | None, arg_text: str):
    if task_id is None:
        return {
            "message_for_user": "[task_start] スレッド内で実行してください。",
            "mode": "free_chat",
        }

    memo = arg_text.strip()
    memo_line = f"- memo   : {memo}\n" if memo else ""

    msg = (
        "[task_start] 学習セッションを開始しました。\n"
        f"- task_id: {task_id}\n"
        f"{memo_line}"
        "※ task_end までの時間が duration に記録されます。"
    )

    return {
        "message_for_user": msg,
        "mode": "task_start",
        "task_id": task_id,
        "memo": memo,
    }


def _handle_task_paused(task_id: str | None):
    if task_id is None:
        return {
            "message_for_user": "[task_paused] スレッド内で実行してください。",
            "mode": "free_chat",
        }

    msg = (
        "[task_paused] 学習を一時停止しました。\n"
        f"- task_id: {task_id}"
    )

    return {
        "message_for_user": msg,
        "mode": "task_paused",
        "task_id": task_id,
    }


def _handle_task_end(task_id: str | None):
    if task_id is None:
        return {
            "message_for_user": "[task_end] スレッド内で実行してください。",
            "mode": "free_chat",
        }

    msg = (
        "[task_end] 学習セッションを終了しました。\n"
        f"- task_id: {task_id}"
    )

    return {
        "message_for_user": msg,
        "mode": "task_end",
        "task_id": task_id,
    }


def _handle_free_chat(raw_text: str, user_id: str | None, context_key: str | None):
    base = raw_text.strip() or "(empty)"

    msg = (
        "[free_chat] タスク管理モード（Persist / Notion 連携）を優先しています。\n"
        f"- user_id: {user_id or 'unknown'}\n"
        f"- context_key: {context_key or 'none'}\n"
        "\n"
        "---- Echo ----\n"
        f"{base}"
    )

    return {
        "message_for_user": msg,
        "mode": "free_chat",
    }