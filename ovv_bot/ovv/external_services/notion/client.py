# ============================================================
# MODULE CONTRACT
# NAME: NotionClient
# LAYER: External Services (EXTERNAL)
# RESPONSIBILITY:
#   - Notion API との通信
#   - HTTP リクエスト（GET/POST/PATCH）
#   - 認証ヘッダ付与
#   - JSON handling（変換のみ）
#   - Executor（ops）からのみ呼ばれる
#   - BIS の境界越境は禁止
# ============================================================

import os
import json
import aiohttp


class NotionClient:
    """
    RESPONSIBILITY TAG: EXTERNAL-SERVICE-NOTION
    - Notion API の通信専用クライアント
    - Executor 層からのみ使用される
    """

    def __init__(self):
        self.base_url = "https://api.notion.com/v1/"
        self.token = os.getenv("NOTION_API_KEY")
        self.version = "2022-06-28"

        if not self.token:
            raise ValueError("NOTION_API_KEY が設定されていません。")

    # ------------------------------
    # internal: HTTP headers
    # ------------------------------
    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }

    # ------------------------------
    # GET
    # ------------------------------
    async def get(self, path: str):
        url = self.base_url + path
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers()) as res:
                return await res.json()

    # ------------------------------
    # POST
    # ------------------------------
    async def post(self, path: str, payload: dict):
        url = self.base_url + path
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), data=json.dumps(payload)) as res:
                return await res.json()

    # ------------------------------
    # PATCH
    # ------------------------------
    async def patch(self, path: str, payload: dict):
        url = self.base_url + path
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, headers=self._headers(), data=json.dumps(payload)) as res:
                return await res.json()