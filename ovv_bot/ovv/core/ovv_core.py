"""
ovv.core.ovv_core
-----------------
BIS アーキテクチャ用 Core v2.0 (Minimal Stable)

Boundary_Gate → Interface_Box → (Core) → Stabilizer

本モジュールは「1 本の入口 run_ovv_core()」のみ外部公開する。
Core は BIS packet(dict) を受け取り、BIS 標準 CoreOutput(dict) を返す。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


# ============================================================
# 型定義
# ============================================================

ModeLiteral = Literal["free_chat", "task_create", "task_start", "task_end"]


class CoreInput(TypedDict, total=False):
    """pipeline → Core に渡される入力ペイロード"""

    input_packet: Dict[str, Any]      # BIS packet
    notion_ops: Any                   # ここでは使わない（将来拡張用）
    state: Dict[str, Any]             # thread-state（dict 想定）


class CoreOutput(TypedDict, total=False):
    """Interface_Box / Stabilizer が受け取る標準 Core 出力"""

    ok: bool
    message_for_user: str
    notion_ops: Any                   # Core では None 固定（NotionOps builder が担当）
    core_mode: str                    # 実際に処理したモード名（command_type ベース）
    new_state: Dict[str, Any]
    debug_log: List[str]


# ============================================================
# 公開 API
# ============================================================

def run_ovv_core(core_payload: Dict[str, Any]) -> CoreOutput:
    """
    Interface_Box → Pipeline → Core という流れで呼ばれる唯一の入口。

    期待される core_payload:
      {
        "input_packet": <BIS packet dict>,
        "notion_ops": None,
        "state": <thread-state dict or None>,
      }
    """

    debug: List[str] = ["[core] enter run_ovv_core"]

    packet: Dict[str, Any] = core_payload.get("input_packet") or {}
    state: Dict[str, Any] = core_payload.get("state") or {}

    command_type: str = str(packet.get("command_type") or "free_chat")
    user_message: str = str(packet.get("raw_content") or "")
    task_id: Optional[str] = packet.get("task_id")

    debug.append(f"[core] command_type = {command_type}")
    debug.append(f"[core] user_message_len = {len(user_message)}")
    debug.append(f"[core] task_id = {task_id}")

    # --------------------------------------------------------
    # モード分岐（command_type ベース）
    # --------------------------------------------------------
    if command_type == "free_chat":
        return _core_free_chat(user_message, state, debug)

    if command_type == "task_create":
        return _core_task_create(user_message, state, debug)

    if command_type == "task_start":
        return _core_task_start(state, debug)

    if command_type == "task_end":
        return _core_task_end(state, debug)

    # 未知のコマンド種別はフォールバック
    debug.append("[core] unknown command_type fallback")

    return {
        "ok": False,
        "message_for_user": (
            "内部エラー: 未知のコマンド種別を受信しました。\n"
            "開発者に 'unknown_core_command_type' と伝えてください。"
        ),
        "notion_ops": None,
        "core_mode": command_type,
        "new_state": state,
        "debug_log": debug,
    }


# 旧呼び出し互換（念のため）
call_core = run_ovv_core
run_core = run_ovv_core


# ============================================================
# モード別ハンドラ
# ============================================================

def _core_free_chat(
    message: str,
    state: Dict[str, Any],
    debug: List[str],
) -> CoreOutput:
    debug.append("[core.free_chat] entered")

    new_state = dict(state)
    new_state["last_message"] = message

    reply = message if message else "メッセージを受信しました。"

    return {
        "ok": True,
        "message_for_user": reply,
        "notion_ops": None,
        "core_mode": "free_chat",
        "new_state": new_state,
        "debug_log": debug,
    }


def _core_task_create(
    message: str,
    state: Dict[str, Any],
    debug: List[str],
) -> CoreOutput:
    debug.append("[core.task_create] entered")

    # Core では NotionOps を生成しない。タイトルだけ決めておき、
    # builders.build_notion_ops 側のフォールバックで create_task を組み立てる想定。
    title = message.strip() or "(無題タスク)"

    reply = f"タスクを作成します: {title}"

    new_state = dict(state)
    new_state["last_created_title"] = title

    return {
        "ok": True,
        "message_for_user": reply,
        "notion_ops": None,
        "core_mode": "task_create",
        "new_state": new_state,
        "debug_log": debug,
    }


def _core_task_start(
    state: Dict[str, Any],
    debug: List[str],
) -> CoreOutput:
    debug.append("[core.task_start] entered")

    # 実際の Persist（session_start）は Stabilizer 側が担当
    reply = "タスクを開始しました。"

    new_state = dict(state)
    new_state["task_active"] = True

    return {
        "ok": True,
        "message_for_user": reply,
        "notion_ops": None,
        "core_mode": "task_start",
        "new_state": new_state,
        "debug_log": debug,
    }


def _core_task_end(
    state: Dict[str, Any],
    debug: List[str],
) -> CoreOutput:
    debug.append("[core.task_end] entered")

    reply = "タスクを終了しました。"

    new_state = dict(state)
    new_state["task_active"] = False

    return {
        "ok": True,
        "message_for_user": reply,
        "notion_ops": None,
        "core_mode": "task_end",
        "new_state": new_state,
        "debug_log": debug,
    }