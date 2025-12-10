# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.3
#
# ROLE:
#   - Boundary_Gate が構築した envelope(dict) を受け取り、
#     Core → NotionOps Builder → Stabilizer のパイプラインを構築する。
#
# INPUT:
#   envelope: dict
#       {
#           "command_type": str,
#           "raw_text": str,
#           "arg_text": str,
#           "context_key": str,
#           "user_id": str,
#       }
#
# OUTPUT:
#   str（Discord に返すメッセージ）
#
# CONSTRAINTS:
#   - Core / Stabilizer とは一方向参照のみ
#   - Persist / Notion へは Stabilizer 経由でのみ触る
#   - task_id = context_key（TEXT）で統一
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional

from ovv.core.ovv_core import run_core
from ovv.external_services.notion.ops.builders import build_notion_ops
from .stabilizer import Stabilizer


# ============================================================
# Public entry
# ============================================================

async def handle_request(envelope: Dict[str, Any]) -> str:
    """
    Boundary_Gate から渡された envelope(dict) を処理し、
    Core → Stabilizer → 最終出力 を構築する。
    """

    # --------------------------------------------------------
    # 1. envelope 正規化
    # --------------------------------------------------------
    command_type = envelope.get("command_type", "free_chat")
    raw_text = envelope.get("raw_text", "")
    arg_text = envelope.get("arg_text", "")

    context_key = envelope.get("context_key")
    user_id = envelope.get("user_id")

    # task_id は context_key と同義（TEXT）
    task_id = str(context_key) if context_key is not None else None

    # --------------------------------------------------------
    # 2. Core に委譲（すべての意思決定は Core）
    # --------------------------------------------------------
    core_input = {
        "command_type": command_type,
        "raw_text": raw_text,
        "arg_text": arg_text,
        "task_id": task_id,
        "context_key": context_key,
        "user_id": user_id,
    }

    core_output: Dict[str, Any] = run_core(core_input)

    # Core 出力例：
    # {
    #   "message_for_user": "Task started.",
    #   "mode": "task_start",   ← builders.py で op に変換するキー
    #   ...
    # }

    message_for_user: str = core_output.get("message_for_user", "")

    # --------------------------------------------------------
    # 3. NotionOps Builder
    # --------------------------------------------------------
    notion_ops = build_notion_ops(core_output, request=_EnvelopeProxy(envelope))

    # --------------------------------------------------------
    # 4. Stabilizer（Persist + NotionOps + Final Message）
    # --------------------------------------------------------
    stabilizer = Stabilizer(
        message_for_user=message_for_user,
        notion_ops=notion_ops,
        context_key=context_key,
        user_id=user_id,
        task_id=task_id,
        command_type=core_output.get("mode"),
        core_output=core_output,
        thread_state=None,   # 将来 ThreadBrain 実装時に使用
    )

    final_message = await stabilizer.finalize()
    return final_message


# ============================================================
# Envelope Proxy for NotionOps Builder
# ------------------------------------------------------------
# build_notion_ops は request.task_id / request.user_meta を参照するため
# envelope(dict) を最低限の属性があるオブジェクト化する。
# ============================================================

class _EnvelopeProxy:
    """
    builders.py の request 引数に対応する最低限の Proxy。
    - .task_id
    - .user_meta
    """

    def __init__(self, envelope: Dict[str, Any]):
        self._envelope = envelope

        # task_id = context_key
        self.task_id = str(envelope.get("context_key")) if envelope.get("context_key") else None

        # user_meta を最低限生成
        user_id = envelope.get("user_id")
        self.user_meta = {
            "user_id": user_id,
            "user_name": user_id,  # Discord ユーザー名を渡したい場合はここで変更可能
        }

    def __repr__(self):
        return f"<EnvelopeProxy task_id={self.task_id}>"