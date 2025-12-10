# ============================================================
# MODULE CONTRACT: External Config / Notion
#
# NAME:
#   config_notion
#
# ROLE:
#   Notion API の資格情報・DB構造・固定パラメータを
#   External Layer（ovv.external_services）に提供する。
#
# DESCRIPTION:
#   - 環境変数の読み込みを集約し、executor / builders / client
#     など External 層のモジュールからは取得経路を隠蔽する。
#   - BIS 層（Boundary / Interface / Stabilizer）から直接参照
#     してはならない。
#
# MUST:
#   - External Services のみが import できる構成。
#   - Notion API Key / Database ID を文字列のまま保持。
#   - INT 丸め込み・型変換は禁止（特に DB ID）。
#
# ============================================================

import os


# ------------------------------------------------------------
# 1. Notion API 認証情報
# ------------------------------------------------------------
NOTION_API_KEY: str | None = os.getenv("NOTION_API_KEY")

if NOTION_API_KEY is None or NOTION_API_KEY.strip() == "":
    raise RuntimeError(
        "[config_notion] NOTION_API_KEY が未設定です。環境変数を確認してください。"
    )


# ------------------------------------------------------------
# 2. Notion Database IDs
#    - すべて TEXT として扱う
#    - INT にキャストしたり、部分一致処理を行ってはならない
# ------------------------------------------------------------

# タスク管理用（Persist v3.0 / BIS TaskFlow と連動）
NOTION_TASK_DB_ID: str | None = os.getenv("NOTION_TASK_DB_ID")
if NOTION_TASK_DB_ID is None or NOTION_TASK_DB_ID.strip() == "":
    raise RuntimeError(
        "[config_notion] NOTION_TASK_DB_ID が未設定です。Notion の DB ID を環境変数に設定してください。"
    )

# 将来拡張 : Knowledge Base
NOTION_KB_DB_ID: str | None = os.getenv("NOTION_KB_DB_ID")

# 将来拡張 : WBS （全タスク統合用）
NOTION_WBS_DB_ID: str | None = os.getenv("NOTION_WBS_DB_ID")


# ------------------------------------------------------------
# 3. 固定ヘッダ（Notion API Contract）
#    - External 層が統一して利用できるように提供
# ------------------------------------------------------------
NOTION_API_VERSION: str = "2022-06-28"

DEFAULT_HEADERS = {
    "Notion-Version": NOTION_API_VERSION,
    # Authorization と Content-Type は client 側で動的構築
}


# ------------------------------------------------------------
# 4. モジュールの公開インターフェース
# ------------------------------------------------------------
__all__ = [
    "NOTION_API_KEY",
    "NOTION_TASK_DB_ID",
    "NOTION_KB_DB_ID",
    "NOTION_WBS_DB_ID",
    "NOTION_API_VERSION",
    "DEFAULT_HEADERS",
]