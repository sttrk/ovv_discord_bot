# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.3 (duration sync)
# ============================================================

from typing import Any, Dict, Optional, List
import datetime

from ovv.external_services.notion.ops.executor import execute_notion_ops
from database.pg import (
    insert_task_session_start,
    insert_task_session_end_and_duration,
    insert_task_log,
)


class Stabilizer:
    """
    BIS 最終出力レイヤ：Persist → NotionOps → Discord への応答を統合する
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
        self.notion_ops = self._normalize_ops(notion_ops)
        self.context_key = context_key
        self.user_id = user_id
        self.task_id = str(task_id) if task_id is not None else None
        self.command_type = command_type

        self.core_output = core_output or {}
        self.thread_state = thread_state or {}

        self._last_duration_seconds: Optional[int] = None

    # 正規化
    @staticmethod
    def _normalize_ops(raw: Any) -> List[Dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            return [raw]
        return []

    # Persist 書き込み
    def _write_persist(self):
        if not self.task_id:
            return

        now = datetime.datetime.utcnow()
        event = self.command_type or "unknown"

        insert_task_log(
            task_id=self.task_id,
            event_type=event,
            content=self.message_for_user or "",
            created_at=now,
        )

        if event == "task_start":
            insert_task_session_start(
                task_id=self.task_id,
                user_id=self.user_id,
                started_at=now,
            )

        elif event == "task_end":
            self._last_duration_seconds = insert_task_session_end_and_duration(
                task_id=self.task_id,
                ended_at=now,
            )

    # Notion duration 同期
    def _augment_notion_ops_with_duration(self) -> List[Dict[str, Any]]:
        ops = list(self.notion_ops)

        if (
            self.command_type == "task_end"
            and self._last_duration_seconds is not None
        ):
            ops.append(
                {
                    "op": "update_task_duration",
                    "task_id": self.task_id,
                    "duration_seconds": self._last_duration_seconds,
                }
            )

        return ops

    # finalize
    async def finalize(self) -> str:
        self._write_persist()

        notion_ops = self._augment_notion_ops_with_duration()
        if notion_ops:
            await execute_notion_ops(
                notion_ops,
                context_key=self.context_key,
                user_id=self.user_id,
            )

        return self.message_for_user