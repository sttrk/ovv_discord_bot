# ovv/external_services/notion/ops/executor.py
# ============================================================
# MODULE CONTRACT: External / NotionOps Executor v2.4
#   (Duration + Summary + Status + SummaryAppend + Trace Observe)
#
# ROLE:
#   - BIS / Stabilizer が構築した NotionOps(list[dict]) を
#     Task DB（NOTION_TASK_DB_ID）へ逐次適用する。
#
# RESPONSIBILITY TAGS:
#   [EXEC_OPS]     ops を順序通り Notion API に適用
#   [TASK_DB]      Task DB（title / status / duration / summary）更新
#   [SUMMARY_APP]  TaskSummary 追記（append_task_summary）
#   [GUARD]        設定不備・Notion無効時の安全ガード
#   [DEBUG]        trace_id 観測ログ（非制御）
#
# CONSTRAINTS:
#   - 呼び出し元は BIS/Stabilizer のみ
#   - Executor は trace_id を生成しない
#   - 1 op 単位で例外 isolation（他の ops は継続）
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, List, Sequence, Union
from datetime import datetime, timezone
import json

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


# ------------------------------------------------------------
# Utility
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_trace_id(op: Dict[str, Any], context_key: str) -> str:
    """
    Executor は trace_id を制御しない。
    観測用として受領のみ行う。
    """
    tid = op.get("trace_id")
    if isinstance(tid, str) and tid:
        return tid

    meta = op.get("meta")
    if isinstance(meta, dict):
        mt = meta.get("trace_id")
        if isinstance(mt, str) and mt:
            return mt

    return str(context_key)


def _log(msg: Dict[str, Any]) -> None:
    print(json.dumps(msg, ensure_ascii=False))


# ============================================================
# Public entry (唯一の外部 API)
# ============================================================

async def execute_notion_ops(
    ops: Union[Dict[str, Any], Sequence[Dict[str, Any]]],
    context_key: str,
    user_id: str,
) -> None:
    """
    BIS / Stabilizer → Executor の唯一の API。
    """

    ops_list: List[Dict[str, Any]] = _normalize_ops(ops)
    if not ops_list:
        return

    notion = get_notion_client()
    if notion is None:
        _log({
            "layer": "NOTION_EXECUTOR",
            "level": "INFO",
            "summary": "notion disabled; skip all ops",
            "context_key": context_key,
        })
        return

    if NOTION_TASK_DB_ID is None:
        _log({
            "layer": "NOTION_EXECUTOR",
            "level": "ERROR",
            "summary": "NOTION_TASK_DB_ID missing; skip all ops",
            "context_key": context_key,
        })
        return

    # --------------------------------------------------------
    # 逐次実行（1 failure = continue）
    # --------------------------------------------------------
    for idx, op_dict in enumerate(ops_list):
        if not isinstance(op_dict, dict):
            continue

        op_name = op_dict.get("op")
        if not op_name:
            continue

        trace_id = _extract_trace_id(op_dict, context_key)
        task_id = op_dict.get("task_id")

        try:
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

            elif op_name == "append_task_summary":
                _append_task_summary(notion, op_dict)

            else:
                _log({
                    "layer": "NOTION_EXECUTOR",
                    "level": "WARN",
                    "trace_id": trace_id,
                    "summary": f"unknown op ignored: {op_name}",
                    "task_id": task_id,
                })

        except Exception as e:
            _log({
                "layer": "NOTION_EXECUTOR",
                "level": "ERROR",
                "trace_id": trace_id,
                "summary": "op execution failed",
                "op": op_name,
                "task_id": task_id,
                "error": {
                    "type": type(e).__name__,
                    "message": str(e),
                },
            })


# ============================================================
# Normalization
# ============================================================

def _normalize_ops(
    raw: Union[None, Dict[str, Any], Sequence[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [op for op in raw if isinstance(op, dict)]
    return []


# ============================================================
# Task Create
# ============================================================

def _create_task_item(notion, ops: Dict[str, Any]) -> None:
    task_id = ops["task_id"]
    task_name = ops.get("task_name", f"Task {task_id}")

    notion.pages.create(
        parent={"database_id": NOTION_TASK_DB_ID},
        properties={
            "name": {"title": [{"text": {"content": task_name}}]},
            "task_id": {"rich_text": [{"text": {"content": task_id}}]},
            "status": {"select": {"name": "not_started"}},
            "created_at": {"date": {"start": _now_iso()}},
            "duration": {"number": 0},
        },
    )


# ============================================================
# Status 更新
# ============================================================

def _update_task_status(notion, ops: Dict[str, Any], status: str) -> None:
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        return

    props: Dict[str, Any] = {"status": {"select": {"name": status}}}
    if status == "in_progress":
        props["started_at"] = {"date": {"start": _now_iso()}}
    elif status == "completed":
        props["ended_at"] = {"date": {"start": _now_iso()}}

    notion.pages.update(page_id=page["id"], properties=props)


# ============================================================
# Duration 更新
# ============================================================

def _update_task_duration(notion, ops: Dict[str, Any]) -> None:
    page = _find_page_by_task_id(notion, ops["task_id"])
    if page is None:
        return
    notion.pages.update(
        page_id=page["id"],
        properties={"duration": {"number": ops["duration_seconds"]}},
    )


# ============================================================
# Summary 更新
# ============================================================

def _update_task_summary(notion, ops: Dict[str, Any]) -> None:
    page = _find_page_by_task_id(notion, ops["task_id"])
    if page is None:
        return

    summary_text = ops.get("summary_text", "")
    if not summary_text:
        return

    notion.pages.update(
        page_id=page["id"],
        properties={
            "summary": {"rich_text": [{"text": {"content": summary_text}}]}
        },
    )


# ============================================================
# Summary 追記（append）
# ============================================================

def _append_task_summary(notion, ops: Dict[str, Any]) -> None:
    page = _find_page_by_task_id(notion, ops["task_id"])
    if page is None:
        return

    append_text = (ops.get("append_text") or "").strip()
    if not append_text:
        return

    current = _get_rich_text_plain(page, "summary").strip()
    new_text = append_text if not current else f"{current}\n{append_text}"

    notion.pages.update(
        page_id=page["id"],
        properties={
            "summary": {"rich_text": [{"text": {"content": new_text}}]}
        },
    )


# ============================================================
# Helpers
# ============================================================

def _get_rich_text_plain(page: Dict[str, Any], prop_name: str) -> str:
    try:
        rt = page.get("properties", {}).get(prop_name, {}).get("rich_text", [])
        return "".join(x.get("plain_text", "") for x in rt if isinstance(x, dict))
    except Exception:
        return ""


def _find_page_by_task_id(notion, task_id: str):
    try:
        res = notion.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={
                "property": "task_id",
                "rich_text": {"equals": task_id},
            },
        )
        items = res.get("results", [])
        return items[0] if items else None
    except Exception:
        return None