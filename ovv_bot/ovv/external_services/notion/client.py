# ============================================================
# MODULE CONTRACT
# NAME: NotionClient
# LAYER: External Services (EXTERNAL)
# RESPONSIBILITY:
#   - Notion API とのHTTP通信
#   - 認証ヘッダの付与
#   - GET / POST / PATCH の実行
#   - JSON <-> dict のシリアライズ
#   - Executor（ops）からのみ呼び出される
#
# INPUT:
#   - path: str
#   - payload: dict（POST/PATCH の場合）
#
# OUTPUT:
#   - Notion API の JSON レスポンス（dict）
#
# MUST NOT:
#   - Core ロジックの実装
#   - Boundary_Gate / Interface_Box / Stabilizer の呼び出し
#   - Discord API や DB へのアクセス
#
# INTERACTION:
#   - Called by: ovv.external_services.notion.ops.executor
#   - Calls: Notion API endpoints
#   - Forbidden: BIS 層（boundary/interface/core/stabilizer）
# ============================================================

import os
import json
import aiohttp


class NotionClient:
    """
    RESPONSIBILITY TAG: EXTERNAL-SERVICE-NOTION
    - Notion API 通信に特化した最下層クライアント
    - Executor 層からのみ利用される（他レイヤは越境禁止）
    """

    def __init__(self):
        self.base_url = "https://api.notion.com/v1/"
        self.token = os.getenv("NOTION_API_KEY")
        self.version = "2022-06-28"

        if not self.token:
            raise ValueError("NOTION_API_KEY が設定されていません。")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }

    async def get(self, path: str):
        url = self.base_url + path
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers()) as res:
                return await res.json()

    async def post(self, path: str, payload: dict):
        url = self.base_url + path
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), data=json.dumps(payload)) as res:
                return await res.json()

    async def patch(self, path: str, payload: dict):
        url = self.base_url + path
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, headers=self._headers(), data=json.dumps(payload)) as res:
                return await res.json()