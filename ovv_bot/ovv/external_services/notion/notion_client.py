from notion_client import Client
from .config_notion import NOTION_API_KEY

# ------------------------------------------------------------
# Notion API Client（単一インスタンス）
# ------------------------------------------------------------

if NOTION_API_KEY:
    notion = Client(auth=NOTION_API_KEY)
else:
    notion = None
    print("[WARN] NOTION_API_KEY is not set → Notion ops disabled.")


def get_notion_client():
    """
    外部からはこの関数を通して client を取得させる。
    （None の場合、executor 側で gracefully degrade）
    """
    return notion
