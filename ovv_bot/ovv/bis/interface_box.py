# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v3.4
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
#   str（Discord に返す最終メッセージ）
#
# CONSTRAINTS:
#   - Core / Stabilizer とは一方向参照のみ
#   - Persist / Notion は Stabilizer 経由でのみ触れる
#   - task_id = context_key（TEXT）で統一
# ============================================================

from __future__ import annotations

from typing import Any, Dict

from ovv.core.ovv_core import run_core
from ovv.external_services.notion.ops.builders import build_notion_ops
from .stabilizer import Stabilizer


# ============================================================
# Public entry
# ============================================================

async def handle_request(envelope: Dict[str, Any]) -> str:
    """
    Boundary_Gate → Interface_Box のメイン処理。
    Core → NotionOpsBuilder → Stabilizer の順に処理する。
    """

    # --------------------------------------------------------
    # 1. envelope 正規化
    # --------------------------------------------------------
    command_type: str = envelope.get("command_type", "free_chat")
    raw_text: str = envelope.get("raw_text", "")
    arg_text: str = envelope.get("arg_text", "")

    context_key = envelope.get("context_key")
    user_id = envelope.get("user_id")

    # task_id = context_key と同義（TEXT）
    task_id = str(context_key) if context_key is not None else None

    # --------------------------------------------------------
    # 2. Core v2.1 に委譲
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

    message_for_user: str = core_output.get("message_for_user", "")
    mode: str = core_output.get("mode", "free_chat")

    # --------------------------------------------------------
    # 3. NotionOps Builder
    # --------------------------------------------------------
    proxy_request = _EnvelopeProxy(envelope)
    notion_ops = build_notion_ops(core_output, request=proxy_request)

    # --------------------------------------------------------
    # 4. Stabilizer（Persist + NotionOps + Final Message）
    # --------------------------------------------------------
    stabilizer = Stabilizer(
        message_for_user=message_for_user,
        notion_ops=notion_ops,
        context_key=context_key,
        user_id=user_id,
        task_id=task_id,
        command_type=mode,
        core_output=core_output,
        thread_state=None,  # ThreadBrain フェーズで活用予定
    )

    final_message = await stabilizer.finalize()
    return final_message


# ============================================================
# Envelope Proxy for NotionOps Builder
# ============================================================

class _EnvelopeProxy:
    """
    build_notion_ops が参照する最小限の request オブジェクト互換。
    - task_id
    - user_meta
    - raw_text / arg_text（将来拡張を考慮して保持）
    """

    def __init__(self, envelope: Dict[str, Any]):
        self._envelope = envelope

        # --- 主要フィールド ---
        context_key = envelope.get("context_key")
        self.task_id = str(context_key) if context_key is not None else None

        # --- user_meta ---
        user_id = envelope.get("user_id")
        self.user_meta = {
            "user_id": user_id,
            "user_name": user_id,  # Discord の表示名を使うならここで置き換え可能
        }

        # --- 拡張用（現時点で builders から参照されないが安全のため保持） ---
        self.raw_text = envelope.get("raw_text", "")
        self.arg_text = envelope.get("arg_text", "")

    def __repr__(self):
        return f"<EnvelopeProxy task_id={self.task_id}>"