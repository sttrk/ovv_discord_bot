# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.10 (Final — WBS Finalize → NotionTaskSummary Append)
#   (Persist v3.0 + NotionOps + Duration + TaskSummary + WBS Finalize Append)
#
# ROLE:
#   - BIS の最終統合レイヤ。
#   - Core / Interface_Box 出力をもとに：
#         [1] Persist v3.0 への書き込み
#         [2] NotionOps の拡張（duration / summary）
#         [3] work_item finalized（done / dropped）を NotionTaskSummary に追記（append）
#         [4] Notion API の逐次実行
#         [5] Discord へ返す最終メッセージ確定
#
# RESPONSIBILITY TAGS:
#   [PERSIST]        task_log / task_session
#   [BUILD_OPS]      duration / summary / finalize_append の ops 構築
#   [EXEC_OPS]       Notion Executor 呼び出し
#   [FINAL]          Discord 出力確定
#   [DEBUG]          pipeline 全体の状態追跡
#
# CONSTRAINTS:
#   - Core → Stabilizer → Executor の一方向のみ
#   - Notion API エラーはログ出力し、実行は継続
#   - duration_seconds の唯一の真は DB（Persist）
#   - summary_text は Core 生成を最優先
#
# SPEC (FINAL):
#   - NotionTaskSummary への移送フォーマットは「追記（append）」で確定
#   - 追記フォーマット（1行）:
#         [WBS:done] <rationale>
#         [WBS:dropped] <rationale>
# ============================================================

from __future__ import annotations

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
# Stabilizer (Final Layer of BIS)
# ------------------------------------------------------------

class Stabilizer:
    """
    BIS Final Layer:
        Persist → NotionOps → Discord Response
    を責務分離に基づいて統合的に制御する。
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
        # Discord 向け最終メッセージ
        self.message_for_user = message_for_user or ""

        # Builder → Stabilizer（list[dict] 化）
        self.notion_ops = self._normalize_ops(notion_ops)

        self.context_key = context_key
        self.user_id = user_id
        self.task_id = str(task_id) if task_id is not None else None

        # Core の mode（例: "task_start", "task_end"）
        self.command_type = command_type

        self.core_output = core_output or {}
        self.thread_state = thread_state or {}

        self._last_duration_seconds: Optional[int] = None

    # ========================================================
    # [BUILD_OPS] NotionOps normalize
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
    def _write_persist(self) -> None:
        if not self.task_id:
            return

        now = datetime.datetime.utcnow()
        event = self.command_type or "unknown"

        # task_log
        try:
            insert_task_log(
                task_id=self.task_id,
                event_type=event,
                content=self.message_for_user or "",
                created_at=now,
            )
        except Exception as e:
            print("[Stabilizer:ERROR] task_log failed:", repr(e))
            traceback.print_exc()

        # task_session start / end
        try:
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
        except Exception as e:
            print("[Stabilizer:ERROR] task_session failed:", repr(e))
            traceback.print_exc()

    # ========================================================
    # [BUILD_OPS] duration ops
    # ========================================================
    def _augment_duration(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if (
            self.command_type == "task_end"
            and self._last_duration_seconds is not None
            and self.task_id
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
    # Internal: finalized work_item (done/dropped) → append line
    # ========================================================
    @staticmethod
    def _format_finalized_append_line(finalized: Dict[str, Any]) -> str:
        status = (finalized.get("status") or "").strip()
        rationale = (finalized.get("rationale") or "").strip()

        # status は最低限のみ許容（逸脱防止）
        if status not in ("done", "dropped"):
            status = "done" if status else "done"

        if not rationale:
            rationale = "<no rationale>"

        return f"[WBS:{status}] {rationale}"

    def _get_finalized_append_text(self) -> str:
        """
        thread_state.finalized_item から NotionTaskSummary 追記テキストを生成。
        """
        finalized = self.thread_state.get("finalized_item")
        if not isinstance(finalized, dict):
            return ""
        return self._format_finalized_append_line(finalized)

    # ========================================================
    # [BUILD_OPS] summary text（Core優先）
    # ========================================================
    def _build_summary_text(self) -> str:
        # Core 明示生成を最優先
        for key in ("task_summary", "summary_text", "task_summary_text"):
            v = self.core_output.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

        # フォールバック：Discord メッセージ
        msg = self.message_for_user.strip()
        if msg:
            return msg

        return ""

    # ========================================================
    # [BUILD_OPS] summary ops（上書き）
    #   - task_paused / task_end のみ
    # ========================================================
    def _augment_summary(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.command_type not in ("task_paused", "task_end"):
            return ops
        if not self.task_id:
            return ops

        summary = self._build_summary_text()
        if not summary:
            print(f"[Stabilizer:DEBUG] No summary_text generated (task_id={self.task_id})")
            return ops

        ops.append(
            {
                "op": "update_task_summary",
                "task_id": self.task_id,
                "summary_text": summary,
            }
        )
        return ops

    # ========================================================
    # [BUILD_OPS] WBS finalized item → TaskSummary append（追記）
    #   - wbs_done / wbs_drop のみ
    #   - summary を上書きしない。追記専用の op を積む。
    # ========================================================
    def _augment_wbs_finalize_append(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.command_type not in ("wbs_done", "wbs_drop"):
            return ops
        if not self.task_id:
            return ops

        append_text = self._get_finalized_append_text()
        if not append_text:
            # finalized_item が無い場合は何もしない（破綻回避）
            return ops

        ops.append(
            {
                "op": "append_task_summary",
                "task_id": self.task_id,
                "append_text": append_text,
            }
        )
        return ops

    # ========================================================
    # [FINAL] Finalizer
    # ========================================================
    async def finalize(self) -> str:
        # -----------------------------------------
        # 1. Persist
        # -----------------------------------------
        try:
            self._write_persist()
        except Exception as e:
            print("[Stabilizer:ERROR] Persist failure:", repr(e))
            traceback.print_exc()

        # -----------------------------------------
        # 2. NotionOps
        # -----------------------------------------
        ops = list(self.notion_ops)
        ops = self._augment_duration(ops)
        ops = self._augment_summary(ops)
        ops = self._augment_wbs_finalize_append(ops)

        if ops:
            print("==== Stabilizer: EXEC_OPS (debug) ====")
            print(" context_key :", self.context_key)
            print(" task_id     :", self.task_id)
            print(" command_type:", self.command_type)
            print(" ops:")
            for op in ops:
                print("   ", op)
            print("======================================")

            try:
                await execute_notion_ops(
                    ops,
                    context_key=self.context_key,
                    user_id=self.user_id,
                )
            except Exception as e:
                print("[Stabilizer:ERROR] execute_notion_ops failed:", repr(e))
                traceback.print_exc()

        # -----------------------------------------
        # 3. Discord 出力
        # -----------------------------------------
        return self.message_for_user