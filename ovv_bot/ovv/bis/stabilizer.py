# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer
#
# ROLE:
#   - Core / NotionOps / Persist を束ね、最終的に Discord に返す文字列を確定する。
#   - NotionOps（外部サービス）を実行する。
#   - Persist v3.0（task_session / task_log）への書き込み起点となる。
#
# INPUT:
#   - message_for_user : str
#   - notion_ops       : dict | None
#   - context_key      : str | None
#   - user_id          : str | None
#   - task_id          : str | None   # Persist v3.0 の主キー（thread_id ベース TEXT）
#   - command_type     : str | None   # "task_create" / "task_start" / "task_end" / "free_chat" 等
#   - core_output      : dict | None  # Core v2.0 の生出力（デバッグ／将来拡張用）
#   - thread_state     : dict | None  # StateManager から取得した thread-state snapshot
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
      - NotionOps を実行する（副作用）
      - Persist v3.0（task_session / task_log）への書き込みを行う

    補足:
      - core_output / thread_state は現状ログ用だが、
        将来的なメトリクス・監査・再試行にも利用できるよう保持しておく。
    """

    def __init__(
        self,
        message_for_user: str,
        notion_ops: Optional[Dict[str, Any]],
        context_key: Optional[str],
        user_id: Optional[str],
        task_id: Optional[str] = None,
        command_type: Optional[str] = None,
        core_output: Optional[Dict[str, Any]] = None,
        thread_state: Optional[Dict[str, Any]] = None,
    ):
        self.message_for_user = message_for_user
        self.notion_ops = notion_ops
        self.context_key = context_key
        self.user_id = user_id
        # task_id は TEXT として扱う（DB 側も TEXT 前提）
        self.task_id = str(task_id) if task_id is not None else None
        self.command_type = command_type
        self.core_output = core_output or {}
        self.thread_state = thread_state or {}

    # --------------------------------------------------------
    # Internal: Persist Writer
    # --------------------------------------------------------
    def _write_persist(self) -> None:
        """
        Persist v3.0 書き込みユニット。

        - task_log : すべてのコマンドで 1 レコード追記
        - task_session :
            - task_start : セッション開始
            - task_end   : セッション終了 + duration 更新
        """

        if not self.task_id:
            # スレッド外（DM や guild 共通チャンネル等）では Persist 書き込みを行わない
            return

        now = datetime.datetime.utcnow()
        event_type = self.command_type or "unknown"

        # --- task_log 追記 ---
        # 将来的に core_output / thread_state を JSON でメタカラムに載せたい場合は、
        # ここでシリアライズするだけでよい。
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
                user_id=self.user_id,
                started_at=now,
            )

        elif self.command_type == "task_end":
            insert_task_session_end_and_duration(
                task_id=self.task_id,
                ended_at=now,
            )

        # "task_create" / "free_chat" 等は session の開始・終了は変更しない
        # （log のみ残す）

    # --------------------------------------------------------
    # FINALIZER
    # --------------------------------------------------------
    async def finalize(self) -> str:
        """
        Final 出力フェーズ。

        順序:
          1. NotionOps 実行
          2. Persist v3.0 書き込み
          3. Discord に返す文字列を返却
        """

        # ---------- 1. NotionOps ----------
        if self.notion_ops:
            await execute_notion_ops(
                self.notion_ops,
                context_key=self.context_key,
                user_id=self.user_id,
            )

        # ---------- 2. Persist 書き込み（同期） ----------
        self._write_persist()

        # ---------- 3. Discord に返す文字列 ----------
        return self.message_for_user