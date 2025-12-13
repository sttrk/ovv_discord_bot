# ovv/core/inference/inference_box.py
# ============================================================
# MODULE CONTRACT: Inference_Box v0.1 (Soft-Lock)
#
# ROLE:
#   - ThreadWBS / PG / Notion を直接参照しない
#   - Snapshot 1枚から「発話すべきか」を判定する
#
# CONSTRAINTS:
#   - Pure function（副作用ゼロ）
#   - if / then テーブルのみ
#   - 命令形・手順提示禁止
# ============================================================

from __future__ import annotations
from typing import Dict, Any


def infer(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """
    Snapshot -> InferenceResult
    """

    task_status = snapshot.get("task_status")
    focus_point = snapshot.get("focus_point")
    work_item_count = int(snapshot.get("work_item_count", 0))
    open_items = int(snapshot.get("open_items", 0))
    open_session = bool(snapshot.get("open_session", False))

    # --------------------------------------------------------
    # 1) 矛盾検知（warning）
    # --------------------------------------------------------
    if task_status == "started" and not open_session:
        return {"type": "warning", "message": "作業状態とセッションが一致していません"}

    if task_status in ("paused", "ended") and open_session:
        return {"type": "warning", "message": "状態に矛盾があります"}

    if focus_point is not None and work_item_count == 0:
        return {"type": "warning", "message": "状態に矛盾があります"}

    if task_status == "started" and open_items == 0:
        return {"type": "warning", "message": "状態に矛盾があります"}

    # --------------------------------------------------------
    # 2) 一意進行（hint）
    # --------------------------------------------------------
    if task_status == "created" and work_item_count > 0:
        return {"type": "hint", "message": "次に進める状態です"}

    if task_status == "paused" and open_items > 0:
        return {"type": "hint", "message": "未処理の項目があります"}

    if task_status == "started" and open_items == 1:
        return {"type": "hint", "message": "未処理の項目があります"}

    if task_status == "started" and focus_point is None and open_items > 0:
        return {"type": "hint", "message": "次に進める状態です"}

    if task_status == "ended" and open_items > 0:
        return {"type": "hint", "message": "未処理の項目があります"}

    # --------------------------------------------------------
    # 3) 沈黙（none）
    # --------------------------------------------------------
    return {"type": "none"}