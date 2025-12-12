# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.9
#   (Persist v3.0 + NotionOps + Duration + TaskSummary + WBS Finalize)
#
# ROLE:
#   - BIS の最終統合レイヤ。
#   - Core / Interface_Box 出力をもとに：
#         [1] Persist v3.0 への書き込み
#         [2] NotionOps の拡張（duration / summary）
#         [3] work_item finalized（done / dropped）の Notion 移送
#         [4] Notion API の逐次実行
#         [5] Discord へ返す最終メッセージ確定
#
# RESPONSIBILITY TAGS:
#   [PERSIST]        task_log / task_session
#   [BUILD_OPS]      duration / summary / finalized_item ops 構築
#   [EXEC_OPS]       Notion Executor 呼び出し
#   [FINAL]          Discord 出力確定
#   [DEBUG]          pipeline 全体の状態追跡
#
# CONSTRAINTS:
#   - Core → Stabilizer → Executor の一方向のみ
#   - Notion API エラーはログ出力し、実行は継続
#   - duration_seconds の唯一の真は DB（Persist）
#   - summary_text は Core 生成を最優先
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional, List, Tuple
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

        # Core の mode / Interface の command_type（例: "task_start", "task_end", "wbs_done"）
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

        # task_log（すべてのイベントで残す）
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

        # task_session start / end（該当イベントのみ）
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
    # Internal: finalized_item extract
    # ========================================================
    def _extract_finalized_item(self) -> Optional[Dict[str, Any]]:
        """
        Interface_Box から渡される thread_state.finalized_item を取得する。
        期待する形（最小）:
          {
            "rationale": str,
            "status": "done" | "dropped",
            "finalized_at": "...(optional)..."
          }
        """
        finalized = self.thread_state.get("finalized_item")
        if not isinstance(finalized, dict):
            return None

        rationale = finalized.get("rationale")
        status = finalized.get("status")
        if not (isinstance(rationale, str) and rationale.strip()):
            return None
        if status not in ("done", "dropped"):
            return None

        out = {
            "rationale": rationale.strip(),
            "status": status,
        }
        fa = finalized.get("finalized_at")
        if isinstance(fa, str) and fa.strip():
            out["finalized_at"] = fa.strip()
        return out

    # ========================================================
    # [BUILD_OPS] summary text
    # ========================================================
    def _build_summary_text(self) -> str:
        # Core 明示生成を最優先
        for key in ("task_summary", "summary_text", "task_summary_text"):
            v = self.core_output.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

        # work_item finalized 由来（最小）
        finalized = self._extract_finalized_item()
        if finalized:
            return f"[WBS:{finalized['status']}] {finalized['rationale']}"

        # フォールバック：Discord メッセージ
        msg = self.message_for_user.strip()
        if msg:
            return msg

        return ""

    # ========================================================
    # [BUILD_OPS] summary ops
    # ========================================================
    def _augment_summary(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # task_paused / task_end / wbs finalize のみ
        if self.command_type not in ("task_paused", "task_end", "wbs_done", "wbs_drop"):
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
    # [BUILD_OPS] finalized_item ops（WBS → NotionTaskSummary 移送）
    # ========================================================
    def _augment_finalized_item(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        v3.9:
          - work_item が done/dropped になった「確定イベント」を Notion に移送する。
        最小実装としては、Notion 側の保存領域が summary であるため、
          - command_type が wbs_done / wbs_drop のとき
          - finalized_item が存在するとき
        に「summary 更新」を確実に走らせる（上書きの是非は NotionOps 側で管理される前提）。
        """
        if self.command_type not in ("wbs_done", "wbs_drop"):
            return ops
        if not self.task_id:
            return ops

        finalized = self._extract_finalized_item()
        if not finalized:
            return ops

        # 既に summary op が追加されている場合は二重追加しない
        already = any(
            isinstance(op, dict) and op.get("op") == "update_task_summary" and op.get("task_id") == self.task_id
            for op in ops
        )
        if already:
            return ops

        summary_text = f"[WBS:{finalized['status']}] {finalized['rationale']}"
        ops.append(
            {
                "op": "update_task_summary",
                "task_id": self.task_id,
                "summary_text": summary_text,
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

        # finalized_item は「移送」なので、summary より先に確実に ops 化しておく
        ops = self._augment_finalized_item(ops)

        # task_paused/task_end/wbs_finalize の summary
        ops = self._augment_summary(ops)

        if ops:
            print("==== Stabilizer: EXEC_OPS (debug) ====")
            print(" context_key :", self.context_key)
            print(" task_id     :", self.task_id)
            print(" command_type:", self.command_type)
            finalized = self._extract_finalized_item()
            if finalized:
                print(" finalized_item:", finalized)
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