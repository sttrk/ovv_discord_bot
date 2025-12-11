# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.6 (Persist + NotionOps + DebugTrace)
#
# ROLE:
#   - BIS の最終統合レイヤ。
#   - Core の出力をもとに：
#         [1] Persist v3.0 への書き込み
#         [2] NotionOps の実行（A案）
#         [3] Discord へ返す最終メッセージの確定
#   を一方向パイプラインで行う。
#
# RESPONSIBILITY TAGS:
#   [PERSIST]   task_log / task_session への書き込み
#   [BUILD_OPS] NotionOps の拡張（duration 追加など）
#   [EXEC_OPS]  Notion API 呼び出し実行
#   [FINAL]     Discord へ返すレスポンス確定
#   [DEBUG]     NotionOps / Persist の内部状態を追跡
#
# CONSTRAINTS:
#   - Core → Stabilizer → 外部I/O のみ。逆参照禁止。
#   - Notion API エラーは握り潰さずログ出力。
#   - duration_seconds は DB の正を唯一の真実とする。
# ============================================================

from typing import Any, Dict, Optional, List
import datetime
import traceback

from ovv.external_services.notion.ops.executor import execute_notion_ops
from database.pg import (
    insert_task_session_start,
    insert_task_session_end_and_duration,
    insert_task_log,
)


# ------------------------------------------------------------
# Stabilizer Class
# ------------------------------------------------------------

class Stabilizer:
    """
    BIS 最終レイヤ：
        Persist → NotionOps → Discord 出力
    を統合的に制御する。
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

    # ========================================================
    # [BUILD_OPS] normalize
    # ========================================================
    @staticmethod
    def _normalize_ops(raw: Any) -> List[Dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            return [raw]
        print("[Stabilizer:DEBUG] unexpected ops type:", type(raw))
        return []

    # ========================================================
    # [PERSIST] Persist v3.0 書き込み
    # ========================================================
    def _write_persist(self):
        if not self.task_id:
            return

        now = datetime.datetime.utcnow()
        event = self.command_type or "unknown"

        # log
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

    # ========================================================
    # [BUILD_OPS] duration ops を NotionOps に連結
    # ========================================================
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

    # ========================================================
    # [FINAL] Finalizer
    # ========================================================
    async def finalize(self) -> str:

        # -----------------------------------------
        # 1. Persist 書き込み
        # -----------------------------------------
        try:
            self._write_persist()
        except Exception as e:
            print("[Stabilizer:ERROR] Persist failure:", repr(e))
            traceback.print_exc()

        # -----------------------------------------
        # 2. NotionOps 実行（デバッグ強化）
        # -----------------------------------------
        notion_ops = self._augment_notion_ops_with_duration()

        if notion_ops:
            print("==== Stabilizer: EXEC_OPS (debug) ====")
            print("context_key:", self.context_key)
            print("task_id:", self.task_id)
            print("ops:", notion_ops)
            print("======================================")

            try:
                await execute_notion_ops(
                    notion_ops,
                    context_key=self.context_key,
                    user_id=self.user_id,
                )
            except Exception as e:
                print("[Stabilizer:ERROR] execute_notion_ops failed:", repr(e))
                traceback.print_exc()

        # -----------------------------------------
        # 3. Discord に返すメッセージ
        # -----------------------------------------
        return self.message_for_user