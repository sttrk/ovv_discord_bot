import os

# ------------------------------------------------------------
# Notion API Keys / Database IDs
# Render の環境変数から取得するだけの「設定ファイル」
# ------------------------------------------------------------

NOTION_API_KEY = os.getenv("NOTION_API_KEY")

# タスク管理DB（Discord タスク = Notion タスク）
NOTION_TASK_DB_ID = os.getenv("NOTION_TASK_DB_ID")

# 今後の拡張用（Knowledge Base / WBS 等）
NOTION_KB_DB_ID = os.getenv("NOTION_KB_DB_ID")
NOTION_WBS_DB_ID = os.getenv("NOTION_WBS_DB_ID")


def validate_notion_config():
    """
    起動時に設定の存在を最低限チェックする補助関数。
    executor / client 側ではこの関数を呼び出さない。
    bot.py の起動時に使う想定。
    """
    if NOTION_API_KEY is None:
        raise RuntimeError("ENV NOTION_API_KEY is missing.")

    if NOTION_TASK_DB_ID is None:
        print("[WARN] NOTION_TASK_DB_ID is not set. Task ops will be disabled.")

    return True