# notion/notion_api.py
# Notion CRUD Layer - Cycle-Free Stable Edition

from datetime import datetime, timezone
from typing import Optional, List, Dict

from notion_client import Client

# ============================================================
# Audit Injection（bot.py から注入される想定）
# ============================================================
log_audit = None  # bot.py 側で notion_api.log_audit = db_pg.log_audit する

# ============================================================
# Notion Client（bot.py から注入）
# ============================================================
notion: Optional[Client] = None
client: Optional[Client] = None  # 互換用（debug_boot が参照する可能性あり）


def inject_notion_client(notion_client: Client):
    """
    bot.py から呼び出して Notion クライアントを注入する。
    """
    global notion, client
    notion = notion_client
    client = notion_client


# ============================================================
# create_task
# ============================================================
def create_task(db_id: str, name: str, goal: str, thread_id: int, channel_id: int):
    """
    Tasks DB にタスクページを 1 件作成する。
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        page = notion.pages.create(
            parent={"database_id": db_id},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "goal": {"rich_text": [{"text": {"content": goal}}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "channel_id": {"rich_text": [{"text": {"content": str(channel_id)}}]},
                "created_at": {"date": {"start": now_iso}},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return page["id"]

    except Exception as e:
        if log_audit:
            log_audit("notion_error", {
                "op": "create_task",
                "name": name,
                "error": repr(e),
            })
        return None


# ============================================================
# start_session
# ============================================================
def start_session(db_id: str, task_id: str, name: str, thread_id: int):
    """
    Sessions DB にセッションページを 1 件作成する。
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        page = notion.pages.create(
            parent={"database_id": db_id},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "task_id": {"relation": [{"id": task_id}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "start_time": {"date": {"start": now_iso}},
                "created_at": {"date": {"start": now_iso}},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return page["id"]

    except Exception as e:
        if log_audit:
            log_audit("notion_error", {
                "op": "start_session",
                "task_id": task_id,
                "error": repr(e),
            })
        return None


# ============================================================
# end_session
# ============================================================
def end_session(page_id: str, summary: str):
    """
    セッション終了時に status / end_time / summary を更新する。
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                "status": {"select": {"name": "completed"}},
                "end_time": {"date": {"start": now_iso}},
                "summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return True

    except Exception as e:
        if log_audit:
            log_audit("notion_error", {
                "op": "end_session",
                "page_id": page_id,
                "error": repr(e),
            })
        return False


# ============================================================
# append_logs
# ============================================================
def append_logs(db_id: str, session_id: str, logs: List[Dict]):
    """
    Logs DB にログページを複数行追加する。
    logs: {author, content, created_at} のリスト
    """
    try:
        for log in logs:
            notion.pages.create(
                parent={"database_id": db_id},
                properties={
                    "_ignore": {"title": [{"text": {"content": "log"}}]},
                    "session_id": {"relation": [{"id": session_id}]},
                    "author": {"rich_text": [{"text": {"content": log["author"]}}]},
                    "content": {"rich_text": [{"text": {"content": log["content"][:2000]}}]},
                    "created_at": {"date": {"start": log["created_at"]}},
                },
            )
        return True

    except Exception as e:
        if log_audit:
            log_audit("notion_error", {
                "op": "append_logs",
                "session_id": session_id,
                "log_count": len(logs),
                "error": repr(e),
            })
        return False
