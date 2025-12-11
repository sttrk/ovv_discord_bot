# ovv/external_services/notion/ops/executor.py
# ============================================================
# MODULE CONTRACT: External / NotionOps Executor v2.1
#
# ROLE:
#   - BIS / Stabilizer から渡された NotionOps(list[dict]) を、
#     Task DB（NOTION_TASK_DB_ID）へ順序通り適用する唯一の層。
#
# RESPONSIBILITY TAGS:
#   [EXEC_OPS]    NotionOps の逐次実行（list の順番を保証）
#   [TASK_DB]     name/title, status, duration, summary を更新
#   [SUMMARY]     task_paused / task_end に伴う summary 書き込み
#   [GUARD]       Notion 無効時 / DB-ID 不備時のガード処理
#
# CONSTRAINTS:
#   - 呼び出し元は Stabilizer のみ（Core/BIS から直接触らない）
#   - ops は論理的に list[dict]、dict 単体は内部で list 化
#   - 例外は飲み込まずログ出力のみ（Stabilizer が Discord へ責任を持つ）
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, List, Sequence, Union
from datetime import datetime, timezone

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


# ============================================================
# Utility
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Public Entry
# ============================================================

async def execute_notion_ops(
    ops: Union[Dict[str, Any], Sequence[Dict[str, Any]]],
    context_key: str,
    user_id: str,
) -> None:
    """
    [EXEC_OPS]
    NotionOps(list[dict]) を Notion TaskDB に適用する唯一の入口。

    - dict 単体でも list 化して順次処理する。
    - 1 つの OP が失敗しても残りを継続する。
    """

    ops_list: List[Dict[str, Any]] = _normalize_ops(ops)
    if not ops_list:
        return

    notion = get_notion_client()
    if notion is None:
        print(f"[NotionOps] Notion disabled → skip (context_key={context_key})")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] Task DB ID missing → skip all ops")
        return

    # --- OPS LOOP ---
    for idx, op_dict in enumerate(ops_list):

        if not isinstance(op_dict, dict):
            print(f"[NotionOps] skip non-dict op[{idx}]: {op_dict!r}")
            continue

        op_name = op_dict.get("op")
        if not op_name:
            print(f"[NotionOps] skip invalid op[{idx}]: missing 'op'")
            continue

        try:
            # -------- OP DISPATCH --------
            if op_name == "task_create":
                _create_task_item(notion, op_dict)

            elif op_name == "task_start":
                _update_task_status(notion, op_dict, status="in_progress")

            elif op_name == "task_paused":
                _update_task_status(notion, op_dict, status="paused")

            elif op_name == "task_end":
                _update_task_status(notion, op_dict, status="completed")

            elif op_name == "update_task_duration":
                _update_task_duration(notion, op_dict)

            elif op_name == "update_task_summary":
                _update_task_summary(notion, op_dict)

            else:
                print(f"[NotionOps] Unknown op[{idx}]: {op_name!r}")

        except Exception as e:
            print(
                f"[NotionOps] Fatal error at op[{idx}] "
                f"(op={op_name!r}, task_id={op_dict.get('task_id')!r}): {e!r}"
            )


# ============================================================
# Normalization
# ============================================================

def _normalize_ops(
    raw: Union[None, Dict[str, Any], Sequence[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """ops を list[dict] へ正規化する（後方互換含む）。"""
    if raw is None:
        return []

    if isinstance(raw, dict):
        return [raw]

    if isinstance(raw, (list, tuple)):
        return [x for x in raw if isinstance(x, dict)]

    print(f"[NotionOps] unexpected ops type: {type(raw)!r}")
    return []


# ============================================================
# [TASK_DB] Task Create
# ============================================================

def _create_task_item(notion, ops: Dict[str, Any]) -> None:
    task_id = ops["task_id"]
    created_by = ops.get("created_by", "")
    task_name = ops.get("task_name", f"Task {task_id}")

    try:
        notion.pages.create(
            parent={"database_id": NOTION_TASK_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": task_name}}]},
                "task_id": {"rich_text": [{"text": {"content": task_id}}]},
                "status": {"select": {"name": "not_started"}},
                "created_by": {"rich_text": [{"text": {"content": created_by}}]},
                "created_at": {"date": {"start": _now_iso()}},
                "started_at": {"date": None},
                "ended_at": {"date": None},
                "duration": {"number": 0},
                "summary": {"rich_text": []},  # 初期空
            },
        )
        print(f"[NotionOps] task_create {task_id}")

    except Exception as e:
        print("[NotionOps] create_task_item error:", repr(e))


# ============================================================
# [TASK_DB] Status 更新
# ============================================================

def _update_task_status(notion, ops: Dict[str, Any], status: str) -> None:
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)

    if page is None:
        print(f"[NotionOps] No such task {task_id}")
        return

    timestamp_prop = {
        "in_progress": "started_at",
        "paused": None,        # paused_at は未採用
        "completed": "ended_at",
        "not_started": None,
    }.get(status)

    properties: Dict[str, Any] = {
        "status": {"select": {"name": status}},
    }

    if timestamp_prop == "started_at":
        properties["started_at"] = {"date": {"start": _now_iso()}}

    elif timestamp_prop == "ended_at":
        properties["ended_at"] = {"date": {"start": _now_iso()}}

    try:
        notion.pages.update(page_id=page["id"], properties=properties)
        print(f"[NotionOps] status → {status} (task_id={task_id})")

    except Exception as e:
        print("[NotionOps] update_status error:", repr(e))


# ============================================================
# [TASK_DB] Duration 更新
# ============================================================

def _update_task_duration(notion, ops: Dict[str, Any]) -> None:
    task_id = ops["task_id"]
    duration_seconds = ops["duration_seconds"]

    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] No such task for duration {task_id}")
        return

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={"duration": {"number": duration_seconds}},
        )
        print(f"[NotionOps] duration update {task_id} = {duration_seconds}")

    except Exception as e:
        print("[NotionOps] duration update error:", repr(e))


# ============================================================
# [SUMMARY] Summary 更新（task_paused / task_end 用）
# ============================================================

def _update_task_summary(notion, ops: Dict[str, Any]) -> None:
    """
    ops = {
        "op": "update_task_summary",
        "task_id": str,
        "summary_text": str,
    }
    """
    task_id = ops["task_id"]
    summary_text = ops.get("summary_text") or ""

    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] No such task for summary {task_id}")
        return

    rich = [{"text": {"content": summary_text[:2000]}}] if summary_text else []

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={
                "summary": {"rich_text": rich},
            },
        )
        print(f"[NotionOps] summary update (task_id={task_id}, len={len(summary_text)})")

    except Exception as e:
        print("[NotionOps] summary update error:", repr(e))


# ============================================================
# Helper
# ============================================================

def _find_page_by_task_id(notion, task_id: str):
    try:
        result = notion.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={
                "property": "task_id",
                "rich_text": {"equals": task_id},
            },
        )
        results = result.get("results", [])
        return results[0] if results else None

    except Exception as e:
        print("[NotionOps] find error:", repr(e))
        return None