# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer
#
# ROLE:
#   - Core / NotionOps / Persist を統合し、Discord に返すべき
#     最終メッセージを確定する。
#   - NotionOps（外部サービス）を実行する。
#   - Persist v3.0（task_session / task_log）への書き込みを行う。
#
# INPUT:
#   - message_for_user : str
#   - notion_ops       : dict | None
#   - context_key      : str | None
#   - user_id          : str | None
#   - task_id          : str | None (TEXT)
#   - command_type     : str | None
#   - core_output      : dict | Any
#   - thread_state     : dict | None
#
# OUTPUT:
#   - str（Discord に返す最終メッセージ）
#
# CONSTRAINT:
#   - Discord API を直接叩かない
#   - Core / Boundary_Gate / Interface_Box を逆参照しない
#   - task_id の丸め込み禁止（必ず TEXT として扱う）
# ============================================================

from typing import Any, Dict, Optional
import datetime

from ovv.external_services.notion.ops.executor import execute_notion_ops

from database.pg import (
    insert_task_session_start,
    insert_task_session_end_and_duration,
    insert_task_log,
)


class Stabilizer:
    """
    BIS 最終レイヤ。  
    External（Notion）、Persist（PostgreSQL）、Core 結果を統括し、
    Discord に返すメッセージを確定する。
    """

    def __init__(
        self,
        *,
        message_for_user: str,
        notion_ops: Optional[Dict[str, Any]],
        context_key: Optional[str],
        user_id: Optional[str],
        task_id: Optional[str],
        command_type: Optional[str],
        core_output: Any,
        thread_state: Optional[Dict[str, Any]],
    ):
        # Output-level
        self.message_for_user = message_for_user

        # External
        self.notion_ops = notion_ops

        # Context metadata
        self.context_key = context_key
        self.user_id = user_id

        # Persist v3.0 keys
        self.task_id = str(task_id) if task_id is not None else None
        self.command_type = command_type

        # Core / State
        self.core_output = core_output
        self.thread_state = thread_state or {}

    # ============================================================
    # Internal: Persist Writer
    # ============================================================
    def _write_persist(self) -> None:
        """
        Persist v3.0 書き込みユニット。

        - task_log（すべての発話で1レコード）
        - task_session（task_start / task_end のみ制御）
        """

        if not self.task_id:
            return  # DM や "タスク外" チャネルは Persist 対象外

        now = datetime.datetime.utcnow()
        event_type = self.command_type or "unknown"

        # ---------- task_log ----------
        insert_task_log(
            task_id=self.task_id,
            event_type=event_type,
            content=self.message_for_user or "",
            created_at=now,
        )

        # ---------- task_session ----------
        if self.command_type == "task_start":
            insert_task_session_start(
                task_id=self.task_id,
                user_id=self.user_id,
                started_at=now,
            )

        elif self.command_type == "task_end":
            insert_task_session_end_and_duration(
                task_id=self.task_id,
                ended_at=now,
            )

        # free_chat や task_create はログのみ（セッションは操作しない）

    # ============================================================
    # FINALIZER
    # ============================================================
    async def finalize(self) -> str:
        """
        出力確定フェーズ。

        順序:
          1. NotionOps 実行
          2. Persist v3.0 書き込み
          3. message_for_user を返す
        """

        # ---------- (1) NotionOps ----------
        if self.notion_ops:
            await execute_notion_ops(
                self.notion_ops,
                context_key=self.context_key,
                user_id=self.user_id,
            )

        # ---------- (2) Persist v3.0 書き込み ----------
        self._write_persist()

        # ---------- (3) Discord へ返す文字列 ----------
        return self.message_for_user