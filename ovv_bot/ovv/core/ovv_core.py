# ovv/core/ovv_core.py
# ============================================================
# Ovv Core v2.2 — Task A案 + TaskSummary A案(min) 対応版
#
# ROLE:
#   - BIS / Interface_Box から受け取った core_input(dict) を解釈し、
#     Task 系コマンド（create / start / paused / end）と free_chat を振り分ける。
#   - Persist / Notion 用のメタ情報（task_name / task_summary など）を組み立てる。
#
# RESPONSIBILITY TAGS:
#   [DISPATCH]  command_type に応じたハンドラ分岐
#   [TASK_META] task_name / task_summary など Task 用メタ情報の構築
#   [USER_MSG]  Discord へ返す message_for_user の生成
#
# CONSTRAINTS:
#   - 外部 I/O（DB / Notion / Discord）は一切行わない（純ロジック層）。
#   - 戻り値は dict のみ。I/O は上位レイヤ（BIS / Stabilizer）で処理する。
# ============================================================

from __future__ import annotations
from typing import Any, Dict


# ============================================================
# Public Entry
# ============================================================

def run_core(core_input: Dict[str, Any]) -> Dict[str, Any]:
    command_type = core_input.get("command_type", "free_chat")
    raw_text = core_input.get("raw_text", "") or ""
    arg_text = core_input.get("arg_text", "") or ""
    task_id = core_input.get("task_id")
    context_key = core_input.get("context_key")
    user_id = core_input.get("user_id")

    # task_id がまだない場合は context_key を fallback として採用
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

def _handle_task_create(task_id: str | None, arg_text: str, user_id: str | None) -> Dict[str, Any]:
    """
    新規タスクの登録。
    - Notion 側では name/title として反映される。
    """
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
        f"- task_id   : {task_id}\n"
        f"- name      : {title}\n"
        f"- created_by: {user_label}"
    )

    return {
        "message_for_user": msg,
        "mode": "task_create",
        "task_name": title,
        "task_id": task_id,
    }


def _handle_task_start(task_id: str | None, arg_text: str) -> Dict[str, Any]:
    """
    学習セッション開始。
    - duration は Persist 側で task_end までの時間として算出される。
    """
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


def _handle_task_paused(task_id: str | None) -> Dict[str, Any]:
    """
    学習一時停止。
    - A案（ミニマム）では、現時点では簡易サマリとして message_for_user をそのまま TaskSummary に流用する。
    - 将来、ThreadBrain / Persist を用いた高度な要約に差し替え可能なよう、task_summary キーを予約する。
    """
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
        # TaskSummary A案 (min): 現段階では Discord 向けメッセージをそのままサマリとして記録
        "task_summary": msg,
    }


def _handle_task_end(task_id: str | None) -> Dict[str, Any]:
    """
    学習セッション終了。
    - duration は Persist v3.0 側で算出 → Stabilizer から Notion に同期。
    - TaskSummary A案（ミニマム）では、終了時点のメッセージを summary として記録する。
    """
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
        # TaskSummary A案 (min): 終了時点の状態をそのまま summary として保存
        "task_summary": msg,
    }


def _handle_free_chat(
    raw_text: str,
    user_id: str | None,
    context_key: str | None,
) -> Dict[str, Any]:
    """
    Task 管理コマンド以外の入力を受けた場合の Fallback。
    - 現フェーズでは、タスク管理モードを優先しつつ、入力をそのまま echo する。
    """
    base = raw_text.strip() or "(empty)"

    msg = (
        "[free_chat] タスク管理モード（Persist / Notion 連携）を優先しています。\n"
        f"- user_id    : {user_id or 'unknown'}\n"
        f"- context_key: {context_key or 'none'}\n"
        "\n"
        "---- Echo ----\n"
        f"{base}"
    )

    return {
        "message_for_user": msg,
        "mode": "free_chat",
    }