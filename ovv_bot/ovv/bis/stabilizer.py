# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.7
#   (Persist + NotionOps + Duration + TaskSummary + DebugTrace)
#
# ROLE:
#   - BIS の最終統合レイヤ。
#   - Core の出力をもとに：
#         [1] Persist v3.0 への書き込み
#         [2] NotionOps の実行（duration / summary 拡張含む）
#         [3] Discord へ返す最終メッセージの確定
#   を一方向パイプラインで行う。
#
# RESPONSIBILITY TAGS:
#   [PERSIST]   task_log / task_session への書き込み
#   [BUILD_OPS] NotionOps の拡張（duration / summary 追加）
#   [EXEC_OPS]  Notion API 呼び出し実行
#   [FINAL]     Discord へ返すレスポンス確定
#   [DEBUG]     NotionOps / Persist の内部状態を追跡
#
# CONSTRAINTS:
#   - Core → Stabilizer → 外部I/O のみ。逆参照禁止。
#   - Notion API エラーは握り潰さずログ出力。
#   - duration_seconds は DB の正を唯一の真実とする。
#   - summary_text は Core 出力があればそれを優先し、なければ安全なフォールバックのみ行う。
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
        # Core が返す mode（"task_create" / "task_start" / "task_paused" / "task_end" 等）
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
    def _write_persist(self) -> None:
        if not self.task_id:
            return

        now = datetime.datetime.utcnow()
        event = self.command_type or "unknown"

        # task_log
        insert_task_log(
            task_id=self.task_id,
            event_type=event,
            content=self.message_for_user or "",
            created_at=now,
        )

        # task_session_start / end
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
    def _augment_notion_ops_with_duration(
        self,
        base_ops: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        ops = list(base_ops if base_ops is not None else self.notion_ops)

        if (
            self.command_type == "task_end"
            and self._last_duration_seconds is not None
            and self.task_id is not None
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
    # [BUILD_OPS] summary ops を NotionOps に連結
    # ========================================================
    def _build_summary_text(self) -> str:
        """
        TaskSummary 用テキストを構築する。

        優先順位:
            1. core_output["task_summary"] (str)
            2. core_output["summary_text"] (str)
            3. core_output["task_summary_text"] (str)
            4. self.message_for_user（フォールバック）
        """
        # Core 側で明示的にサマリを生成している場合
        for key in ("task_summary", "summary_text", "task_summary_text"):
            val = self.core_output.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

        # 暫定フォールバック：ユーザー向けメッセージをそのまま記録
        if isinstance(self.message_for_user, str) and self.message_for_user.strip():
            return self.message_for_user.strip()

        return ""

    def _augment_notion_ops_with_summary(
        self,
        base_ops: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        対象イベント:
            - task_paused (!tp)
            - task_end    (!tc)
        のタイミングで、Notion Task DB の summary を更新する OP を追加する。
        """
        ops = list(base_ops if base_ops is not None else self.notion_ops)

        if self.task_id is None:
            return ops

        if self.command_type not in ("task_paused", "task_end"):
            return ops

        summary_text = self._build_summary_text()
        if not summary_text:
            print(
                "[Stabilizer:DEBUG] no summary_text built "
                f"(task_id={self.task_id}, command_type={self.command_type})"
            )
            return ops

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
        # 1. Persist 書き込み
        # -----------------------------------------
        try:
            self._write_persist()
        except Exception as e:
            print("[Stabilizer:ERROR] Persist failure:", repr(e))
            traceback.print_exc()

        # -----------------------------------------
        # 2. NotionOps 実行（duration / summary 拡張 + デバッグ）
        # -----------------------------------------
        # 2-1. duration 付与
        notion_ops = self._augment_notion_ops_with_duration()

        # 2-2. summary 付与（!tp / !tc）
        notion_ops = self._augment_notion_ops_with_summary(notion_ops)

        if notion_ops:
            print("==== Stabilizer: EXEC_OPS (debug) ====")
            print("context_key:", self.context_key)
            print("task_id:", self.task_id)
            print("command_type:", self.command_type)
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