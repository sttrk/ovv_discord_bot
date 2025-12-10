"""
ovv.core.ovv_core
-----------------
BIS アーキテクチャ用 Core v2.0 (Minimal)

Boundary_Gate → Interface_Box → (ここ) → Stabilizer

このモジュールは、BIS からの標準入力 dict を受け取り、
標準出力 dict を返す「1 本の入口」だけを提供する。

外部から呼び出すのは run_ovv_core(core_input: dict) のみを想定する。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict, Optional


# -----------------------------
# 型定義（ゆるめの TypedDict）
# -----------------------------


ModeLiteral = Literal["free_chat", "task_create", "task_start", "task_end"]


class CoreInput(TypedDict, total=False):
    mode: ModeLiteral
    user_message: str
    thread_id: str
    user_id: str
    state: Dict[str, Any]
    context: Dict[str, Any]


class CoreOutput(TypedDict, total=False):
    ok: bool
    mode: ModeLiteral
    message_for_user: str
    new_state: Dict[str, Any]
    notion_ops: List[Dict[str, Any]]
    pg_ops: List[Dict[str, Any]]
    debug_log: List[str]


# -----------------------------
# 公開 API
# -----------------------------


def run_ovv_core(core_input: Dict[str, Any]) -> CoreOutput:
    """
    BIS から呼び出される唯一のエントリポイント。

    Parameters
    ----------
    core_input : dict
        Boundary_Gate / Interface_Box で組み立てられた入力ペイロード。
        期待される主なキー:
            - mode: "free_chat" / "task_create" / ...
            - user_message: ユーザーの生メッセージ
            - thread_id: Discord のチャンネル or スレ ID
            - user_id: Discord のユーザー ID
            - state: スレ別の永続状態 (dict)
            - context: それ以外の補足情報 (dict)

    Returns
    -------
    CoreOutput : dict
        Stabilizer がそのまま扱える標準フォーマット。
    """

    debug_log: List[str] = []
    safe_mode: ModeLiteral = _normalize_mode(core_input.get("mode"))
    user_message: str = str(core_input.get("user_message") or "").strip()
    state: Dict[str, Any] = _ensure_dict(core_input.get("state"))
    context: Dict[str, Any] = _ensure_dict(core_input.get("context"))

    debug_log.append(f"[core] mode={safe_mode}")
    debug_log.append(f"[core] user_message_len={len(user_message)}")
    debug_log.append(f"[core] state_keys={list(state.keys())}")
    debug_log.append(f"[core] context_keys={list(context.keys())}")

    # 将来はここでモード別に Core ロジックを分岐させる。
    if safe_mode == "free_chat":
        message_for_user, new_state, extra_logs = _handle_free_chat(
            user_message=user_message,
            state=state,
            context=context,
        )
        debug_log.extend(extra_logs)
    elif safe_mode == "task_create":
        message_for_user, new_state, extra_logs = _handle_task_create(
            user_message=user_message,
            state=state,
            context=context,
        )
        debug_log.extend(extra_logs)
    elif safe_mode == "task_start":
        message_for_user, new_state, extra_logs = _handle_task_start(
            user_message=user_message,
            state=state,
            context=context,
        )
        debug_log.extend(extra_logs)
    elif safe_mode == "task_end":
        message_for_user, new_state, extra_logs = _handle_task_end(
            user_message=user_message,
            state=state,
            context=context,
        )
        debug_log.extend(extra_logs)
    else:
        # ここには来ない想定だが、防御的に実装しておく
        message_for_user = (
            "内部エラー: 未知のモードを受信しました。"
            "開発者に 'unknown_core_mode' と伝えてください。"
        )
        new_state = state
        debug_log.append("[core] unexpected mode branch reached")

    core_output: CoreOutput = {
        "ok": True,
        "mode": safe_mode,
        "message_for_user": message_for_user,
        "new_state": new_state,
        "notion_ops": [],  # 今は空で返す（将来 PG/Notion 実装時に拡張）
        "pg_ops": [],
        "debug_log": debug_log,
    }

    return core_output


# 古い呼び出しとの互換用エイリアス（もしあれば）
# どこかで call_core(...), run_core(...) を使っていても壊さないため。
call_core = run_ovv_core
run_core = run_ovv_core


# -----------------------------
# 内部ヘルパ
# -----------------------------


def _normalize_mode(mode: Optional[str]) -> ModeLiteral:
    if mode in ("task_create", "task_start", "task_end", "free_chat"):
        return mode  # type: ignore[return-value]
    # デフォルトは free_chat とみなす
    return "free_chat"


def _ensure_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


# -----------------------------
# モード別ハンドラ（Minimal 実装）
# -----------------------------


def _handle_free_chat(
    user_message: str,
    state: Dict[str, Any],
    context: Dict[str, Any],
) -> tuple[str, Dict[str, Any], List[str]]:
    """
    当面の free_chat の挙動:
    - まだ Core 側の高度な推論は作り込まない
    - 「ユーザー発話をそのまま返す + α」程度に留める
    - state["last_user_message"] を更新しておく
    """
    logs: List[str] = ["[core.free_chat] entered"]

    if not user_message:
        reply = "メッセージが空でした。もう一度入力してください。"
        logs.append("[core.free_chat] empty user_message")
    else:
        # 最小限のエコー（Stabilizer で最終整形される想定）
        reply = user_message
        logs.append("[core.free_chat] echo reply generated")

    # 状態更新
    new_state = dict(state)
    new_state["last_user_message"] = user_message

    return reply, new_state, logs


def _handle_task_create(
    user_message: str,
    state: Dict[str, Any],
    context: Dict[str, Any],
) -> tuple[str, Dict[str, Any], List[str]]:
    """
    将来 Notion Task 作成用に拡張する。
    現状は「未実装です」と返すだけのダミー。
    """
    logs: List[str] = ["[core.task_create] entered (stub)"]

    reply = (
        "タスク作成モードはまだ Core 側が未実装です。\n"
        "現在は free_chat のみ安定動作対象になっています。"
    )

    new_state = dict(state)
    return reply, new_state, logs


def _handle_task_start(
    user_message: str,
    state: Dict[str, Any],
    context: Dict[str, Any],
) -> tuple[str, Dict[str, Any], List[str]]:
    logs: List[str] = ["[core.task_start] entered (stub)"]

    reply = (
        "タスク開始モードはまだ Core 側が未実装です。\n"
        "タスク管理コマンドは、安定版に到達後に有効化します。"
    )

    new_state = dict(state)
    return reply, new_state, logs


def _handle_task_end(
    user_message: str,
    state: Dict[str, Any],
    context: Dict[str, Any],
) -> tuple[str, Dict[str, Any], List[str]]:
    logs: List[str] = ["[core.task_end] entered (stub)"]

    reply = (
        "タスク終了モードはまだ Core 側が未実装です。\n"
        "完了報告のフローは今後のバージョンで追加されます。"
    )

    new_state = dict(state)
    return reply, new_state, logs