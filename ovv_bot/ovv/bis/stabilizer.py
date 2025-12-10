# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.3
#
# Persist v3.0 / NotionOps / duration_time 同期に完全対応
# ============================================================

from typing import Any, Dict, Optional, List
import datetime

from ovv.external_services.notion.ops.executor import execute_notion_ops
from database.pg import (
    insert_task_session_start,
    insert_task_session_end_and_duration,
    insert_task_log,
)
from database.pg import get_task_duration_seconds  # ← 新規に必要（下で説明）


class Stabilizer:
    """
    BIS 最終出力レイヤ。

    責務:
      - Discord に返すメッセージを確定する
      - Persist v3.0（task_session / task_log）への書き込みを行う
      - NotionOps を実行する（副作用）
      - task_end 時に duration_seconds を Notion に同期する
    """

    def __init__(
        self,
        message_for_user: str,
        notion_ops: Optional[Any],
        context_key: Optional[str],
        user_id: Optional[str],
        task_id: Optional[str] = None,
        command_type: Optional[str] = None,
        core_output: Optional[Dict[str, Any]] = None,
        thread_state: Optional[Dict[str, Any]] = None,
    ):
        self.message_for_user = message_for_user or ""
        self.notion_ops: List[Dict[str, Any]] = self._normalize_ops(notion_ops)
        self.context_key = context_key
        self.user_id = user_id
        self.task_id = str(task_id) if task_id is not None else None
        self.command_type = command_type

        self.core_output = core_output or {}
        self.thread_state = thread_state or {}

        # Persist 結果としての duration_seconds（task_end でのみセット）
        self._last_duration_seconds: Optional[int] = None

    # ------------------------------------------------------------
    @staticmethod
    def _normalize_ops(raw: Any) -> List[Dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [op for op in raw if isinstance(op, dict)]
        if isinstance(raw, dict):
            return [raw]
        print("[Stabilizer] unexpected notion_ops type:", type(raw))
        return []

    # ------------------------------------------------------------
    # Persist Writer
    # ------------------------------------------------------------
    def _write_persist(self) -> None:
        """
        Persist v3.0 書き込み
        """

        if not self.task_id:
            return

        now = datetime.datetime.utcnow()
        event_type = self.command_type or "unknown"

        # --- task_log ---
        insert_task_log(
            task_id=self.task_id,
            event_type=event_type,
            content=self.message_for_user or "",
            created_at=now,
        )

        # --- task_session ---
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

            # ここで DB から duration_seconds を SELECT する（PG 側は返り値を返さないため）
            self._last_duration_seconds = get_task_duration_seconds(self.task_id)

    # ------------------------------------------------------------
    # NotionOps 拡張
    # ------------------------------------------------------------
    def _augment_notion_ops_with_duration(self) -> List[Dict[str, Any]]:
        ops = list(self.notion_ops)

        if (
            self.command_type == "task_end"
            and self.task_id
            and self._last_duration_seconds is not None
        ):
            ops.append(
                {
                    "type": "update_task_duration",
                    "task_id": self.task_id,
                    "duration_seconds": self._last_duration_seconds,
                }
            )

        return ops

    # ------------------------------------------------------------
    # FINALIZER
    # ------------------------------------------------------------
    async def finalize(self) -> str:
        # 1. Persist
        self._write_persist()

        # 2. NotionOps（duration 同期込み）
        notion_ops = self._augment_notion_ops_with_duration()
        if notion_ops:
            await execute_notion_ops(
                notion_ops,
                context_key=self.context_key,
                user_id=self.user_id,
            )

        # 3. Discord 出力
        return self.message_for_user