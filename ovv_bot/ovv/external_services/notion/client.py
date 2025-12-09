# ovv/external_services/notion/client.py
# ============================================================
# MODULE CONTRACT: External Service / Notion / Client
#
# ROLE:
#   - Notion API との実通信を担当する唯一の層。
#   - Executor から呼び出され、Notion への副作用操作を実行。
#
# INPUT:
#   - dict 形式の Notion API payload
#
# OUTPUT:
#   - Notion API の結果（必要なら返却）
#
# NOTE:
#   - ExternalContract で定義した API 操作だけを公開する。
#   - Ovv 内部のどの層も Notion API を直接叩いてはならない。
# ============================================================

import os
import aiohttp

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionClient:
    def __init__(self):
        token = os.getenv("NOTION_API_TOKEN")
        if not token:
            raise RuntimeError("NOTION_API_TOKEN が設定されていません。")

        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    # --------------------------------------------------------
    # 基本操作：ページ作成
    # --------------------------------------------------------
    async def create_page(self, payload: dict):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{NOTION_API_BASE}/pages",
                headers=self.headers,
                json=payload,
            ) as resp:
                return await resp.json()

    # --------------------------------------------------------
    # ページ更新
    # --------------------------------------------------------
    async def update_page(self, payload: dict):
        page_id = payload.get("page_id")
        data = payload.get("data")
        if not page_id:
            raise ValueError("payload に page_id が必要です。")

        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{NOTION_API_BASE}/pages/{page_id}",
                headers=self.headers,
                json=data,
            ) as resp:
                return await resp.json()

    # --------------------------------------------------------
    # ブロック追加
    # --------------------------------------------------------
    async def append_block(self, payload: dict):
        block_id = payload.get("block_id")
        children = payload.get("children")
        if not block_id:
            raise ValueError("payload に block_id が必要です。")

        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{NOTION_API_BASE}/blocks/{block_id}/children",
                headers=self.headers,
                json={"children": children},
            ) as resp:
                return await resp.json()