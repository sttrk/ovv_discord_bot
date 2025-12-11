# ovv/bis/stabilizer.py
# ============================================================
# Stabilizer v3.3 — paused / duration / Persist v3.0 完全対応
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
        self.task_id = str(task_id) if task_id else None
        self.command_type = command_type

        self.core_output = core_output or {}
        self.thread_state = thread_state or {}

        self._last_duration_seconds: Optional[int] = None

    @staticmethod
    def _normalize_ops(raw: Any) -> List[Dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            return [raw]
        print("[Stabilizer] invalid notion_ops type:", type(raw))
        return []

    # ------------------------------------------------------------
    # Persist Writer
    # ------------------------------------------------------------

    def _write_persist(self):

        if not self.task_id:
            return

        now = datetime.datetime.utcnow()
        event = self.command_type or "unknown"

        insert_task_log(
            task_id=self.task_id,
            event_type=event,
            content=self.message_for_user,
            created_at=now,
        )

        if self.command_type == "task_start":
            insert_task_session_start(
                task_id=self.task_id,
                user_id=self.user_id,
                started_at=now,
            )

        elif self.command_type in ("task_paused", "task_end"):
            self._last_duration_seconds = insert_task_session_end_and_duration(
                task_id=self.task_id,
                ended_at=now,
            )

    # ------------------------------------------------------------
    # NotionOps Augmentation
    # ------------------------------------------------------------

    def _augment_notion_ops(self):

        ops = list(self.notion_ops)

        if (
            self.command_type in ("task_paused", "task_end")
            and self.task_id
            and self._last_duration_seconds is not None
        ):
            ops.append({
                "op": "update_duration",
                "task_id": self.task_id,
                "duration_seconds": self._last_duration_seconds,
            })

        return ops

    # ------------------------------------------------------------
    # FINAL
    # ------------------------------------------------------------

    async def finalize(self) -> str:
        self._write_persist()

        ops = self._augment_notion_ops()

        if ops:
            for op in ops:
                await execute_notion_ops(op, self.context_key, self.user_id)

        return self.message_for_user