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
#   - task_id          : str | None   # Persist v3.0 の主キー（thread_id）
#
# OUTPUT:
#   - str（Discord に返す最終メッセージ）
#
# CONSTRAINT:
#   - Discord API を直接叩かない。
#   - Core / Boundary_Gate / Interface_Box へ逆参照しない。
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

    Persist 書き込み方針:
      - task_id を TEXT としてそのまま保つ（丸め込み禁止）
      - コマンド種別により task_session / task_log の振る舞いを分岐
    """

    def __init__(
        self,
        message_for_user: str,
        notion_ops: Optional[Dict[str, Any]],
        context_key: Optional[str],
        user_id: Optional[str],
        task_id: Optional[str] = None,
    ):
        self.message_for_user = message_for_user
        self.notion_ops = notion_ops
        self.context_key = context_key
        self.user_id = user_id
        self.task_id = task_id  # Persist v3.0 のキー（thread_id ベース TEXT）

    # ------------------------------------------------------------
    # Internal: Persist Writer
    # ------------------------------------------------------------
    async def _write_persist(self, command_type: Optional[str]):
        """
        Persist v3.0 書き込みユニット。
        Boundary_Gate → InterfaceBox → Stabilizer で渡された情報を元に、
        task_session / task_log を更新する。
        """

        if not self.task_id:
            # スレッド外（DM や guild チャット）では Persist 書き込みを行わない
            return

        now = datetime.datetime.utcnow()

        # task_log（全コマンド共通）
        await insert_task_log(
            task_id=self.task_id,
            event_type=command_type or "unknown",
            content=self.message_for_user or "",
            created_at=now,
        )

        # task_session（開始・終了のみ）
        if command_type == "task_start":
            await insert_task_session_start(
                task_id=self.task_id,
                user_id=self.user_id,
                started_at=now,
            )

        elif command_type == "task_end":
            await insert_task_session_end_and_duration(
                task_id=self.task_id,
                ended_at=now,
            )

        # free_chat / task_create は session を変更しない（log のみ）

    # ------------------------------------------------------------
    # FINALIZER
    # ------------------------------------------------------------
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

        # ---------- 2. Persist 書き込み ----------
        # context_key には command_type が含まれていないため、
        # InterfaceBox の packet から command_type を取得し Stabilizer 初期化時に渡すよう仕様化するか？
        # 現行設計では Stabilizer に command_type が渡っていないため、改修が必要。
        #
        # Persist v3.0 を正しく動作させるため、Stabilizer に command_type を追加で渡す必要がある。
        # 一時措置として context_key からは判別できないため "free_chat" として扱う。
        # 次ステップで InterfaceBox → Stabilizer に command_type を正式追加する。

        await self._write_persist(command_type="free_chat")

        # ---------- 3. Discord に返す文字列 ----------
        return self.message_for_user