# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.2
#
# ROLE:
#   - Core / NotionOps / Persist を束ね、最終的に Discord に返す文字列を確定する。
#   - NotionOps（外部サービス）を実行する。
#   - Persist v3.0（task_session / task_log）への書き込み起点となる。
#
# INPUT:
#   - message_for_user : str
#   - notion_ops       : dict | list[dict] | None
#   - context_key      : str | None
#   - user_id          : str | None
#   - task_id          : str | None   # Persist v3.0 の主キー（thread_id ベース TEXT）
#   - command_type     : str | None   # "task_create" / "task_start" / "task_end" / "free_chat" 等
#   - core_output      : dict | None  # Core v2.0 の生出力（将来拡張用）
#   - thread_state     : dict | None  # StateManager が保持していた thread-state（将来拡張用）
#
# OUTPUT:
#   - str（Discord に返す最終メッセージ）
#
# CONSTRAINT:
#   - Discord API を直接叩かない（呼び出し元が行う）。
#   - Core / Boundary_Gate / Interface_Box を逆参照しない。
#   - task_id は TEXT として扱い、数値への変換・丸め込みを行わない。
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
    BIS 最終出力レイヤ。

    責務:
      - Discord に返すメッセージを確定する
      - Persist v3.0（task_session / task_log）への書き込みを行う
      - NotionOps を実行する（副作用）
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
        # NotionOps は単一 dict を前提に正規化（list が来た場合は先頭のみ使用）
        self.notion_ops: Optional[Dict[str, Any]] = self._normalize_ops(notion_ops)
        self.context_key = context_key
        self.user_id = user_id
        # task_id は TEXT として扱う（DB 側も TEXT 前提）
        self.task_id = str(task_id) if task_id is not None else None
        self.command_type = command_type

        # 将来拡張用（今はほぼ保持のみ）
        self.core_output = core_output or {}
        self.thread_state = thread_state or {}

    # ------------------------------------------------------------
    # Internal: NotionOps 正規化
    # ------------------------------------------------------------
    @staticmethod
    def _normalize_ops(raw: Any) -> Optional[Dict[str, Any]]:
        """
        notion_ops の型ゆれを吸収して dict に正規化する。

        - None           → None
        - dict           → dict
        - list[dict]     → 先頭の dict のみ採用
        - それ以外       → None（ログのみ）
        """
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    return item
            print("[Stabilizer] notion_ops list に dict が含まれていません。→ 無視します。")
            return None

        print("[Stabilizer] unexpected notion_ops type:", type(raw))
        return None

    # ------------------------------------------------------------
    # Internal: Persist Writer
    # ------------------------------------------------------------
    def _write_persist(self) -> None:
        """
        Persist v3.0 書き込みユニット。

        - task_log : すべてのコマンドで 1 レコード追記
        - task_session :
            - task_start : セッション開始（なければ INSERT / あれば更新）
            - task_end   : セッション終了 + duration_seconds 更新
        """

        if not self.task_id:
            # スレッド外（DM や guild 共通チャンネル等）では Persist 書き込みを行わない
            return

        now = datetime.datetime.utcnow()
        event_type = self.command_type or "unknown"

        # --- task_log 追記 ---
        insert_task_log(
            task_id=self.task_id,
            event_type=event_type,
            content=self.message_for_user or "",
            created_at=now,
        )

        # --- task_session 更新 ---
        if self.command_type == "task_start":
            insert_task_session_start(
                task_id=self.task_id,
                user_id=self.user_id or "",
                started_at=now,
            )

        elif self.command_type == "task_end":
            # duration_seconds を計算して DB に反映（戻り値は現在は使用しない）
            insert_task_session_end_and_duration(
                task_id=self.task_id,
                ended_at=now,
            )

        # "task_create" / "free_chat" 等は session の開始・終了は変更しない
        # （log のみ残す）

    # ------------------------------------------------------------
    # FINALIZER
    # ------------------------------------------------------------
    async def finalize(self) -> str:
        """
        Final 出力フェーズ。

        順序:
          1. Persist v3.0 書き込み（task_log / task_session / duration_seconds）
          2. NotionOps 実行
          3. Discord に返す文字列を返却
        """

        # ---------- 1. Persist 書き込み（同期） ----------
        # psycopg2 ベースのため、現状は同期 I/O として呼び出す。
        self._write_persist()

        # ---------- 2. NotionOps ----------
        if self.notion_ops is not None:
            await execute_notion_ops(
                self.notion_ops,
                context_key=self.context_key or "",
                user_id=self.user_id or "",
            )

        # ---------- 3. Discord に返す文字列 ----------
        return self.message_for_user