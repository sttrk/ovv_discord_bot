"""
ovv.core.ovv_core
-----------------
BIS アーキテクチャ用 Core v2.0 (Minimal Stable)

Boundary_Gate → Interface_Box → (Core) → Stabilizer

本モジュールは「1 本の入口 run_ovv_core()」のみ外部公開する。
Core は BIS packet(dict) を受け取り、BIS 標準 CoreOutput(dict) を返す。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Literal, TypedDict


# ============================================================
# 型定義
# ============================================================

ModeLiteral = Literal["free_chat", "task_create", "task_start", "task_end"]


class CoreInput(TypedDict, total=False):
    packet: Dict[str, Any]          # BIS packet
    input_packet: Dict[str, Any]    # pipeline 互換
    notion_ops: Any
    state: Dict[str, Any]


class CoreOutput(TypedDict, total=False):
    ok: bool
    message_for_user: str
    notion_ops: Any
    core_mode: str
    new_state: Dict[str, Any]
    debug_log: List[str]


# ============================================================
# 公開 API
# ============================================================

def run_ovv_core(core_payload: Dict[str, Any]) -> CoreOutput:
    """
    Interface_Box → Pipeline → ここ（Core）という流れで呼ばれる。

    期待される core_payload:
      {
        "input_packet": BIS packet(dict),
        "notion_ops": None,
        "state": StateManager
      }

    Core の責務:
      - command_type に応じた最低限の処理
      - message_for_user の生成
      - new_state の返却
      - notion_ops は builder に任せる前提なのでここでは生成しない
    """

    debug: List[str] = ["[core] enter run_ovv_core"]

    packet = core_payload.get("input_packet", {})
    state = core_payload.get("state") or {}

    command_type: str = packet.get("command_type", "free_chat")
    user_message: str = packet.get("raw_content") or ""
    task_id: str | None = packet.get("task_id")

    debug.append(f"[core] command_type = {command_type}")
    debug.append(f"[core] user_message = {user_message}")
    debug.append(f"[core] task_id = {task_id}")

    # ------------------------------------------------------------
    # モード分岐
    # ------------------------------------------------------------
    if command_type == "free_chat":
        return _core_free_chat(user_message, state, debug)

    elif command_type == "task_create":
        return _core_task_create(user_message, state, debug)

    elif command_type == "task_start":
        return _core_task_start(state, debug)

    elif command_type == "task_end":
        return _core_task_end(state, debug)

    # フォールバック（未知モード）
    debug.append("[core] unknown mode fallback")
    return {
        "ok": False,
        "message_for_user": "内部エラー: 未知のコマンド種別を受信しました。",
        "new_state": state,
        "notion_ops": None,
        "core_mode": command_type,
        "debug_log": debug,
    }


# ============================================================
# モード別ハンドラ
# ============================================================

def _core_free_chat(message: str, state: Dict[str, Any], debug: List[str]) -> CoreOutput:
    debug.append("[core.free_chat]")

    new_state = dict(state)
    new_state["last_message"] = message

    # Echo（最小応答）
    reply = message if message else "メッセージを受信しました。"

    return {
        "ok": True,
        "message_for_user": reply,
        "new_state": new_state,
        "notion_ops": None,
        "core_mode": "free_chat",
        "debug_log": debug,
    }


def _core_task_create(message: str, state: Dict[str, Any], debug: List[str]) -> CoreOutput:
    debug.append("[core.task_create]")

    # ここでは NotionOps を Core で生成しない
    # builder 側の fallback が title → create_task op を構築する

    title = message or "(無題タスク)"
    reply = f"タスクを作成します: {title}"

    new_state = dict(state)
    new_state["last_created_title"] = title

    return {
        "ok": True,
        "message_for_user": reply,
        "new_state": new_state,
        "notion_ops": None,
        "core_mode": "task_create",
        "debug_log": debug,
    }


def _core_task_start(state: Dict[str, Any], debug: List[str]) -> CoreOutput:
    debug.append("[core.task_start]")

    # Persist v3.0 の session_start は Stabilizer が行う
    reply = "タスクを開始しました。"

    new_state = dict(state)
    new_state["task_active"] = True

    return {
        "ok": True,
        "message_for_user": reply,
        "new_state": new_state,
        "notion_ops": None,
        "core_mode": "task_start",
        "debug_log": debug,
    }


def _core_task_end(state: Dict[str, Any], debug: List[str]) -> CoreOutput:
    debug.append("[core.task_end]")

    reply = "タスクを終了しました。"

    new_state = dict(state)
    new_state["task_active"] = False

    return {
        "ok": True,
        "message_for_user": reply,
        "new_state": new_state,
        "notion_ops": None,
        "core_mode": "task_end",
        "debug_log": debug,
    }