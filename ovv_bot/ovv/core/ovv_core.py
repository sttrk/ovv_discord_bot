# ovv/core/ovv_core.py
# ============================================================
# Ovv Core v2.4 — STEP B (ThreadWBS Read-Only Context + CDC Candidate Output)
#
# ROLE:
#   - BIS / Interface_Box から受け取った core_input(dict) を解釈し、
#     Task 系コマンド（create / start / paused / end）と WBS 系コマンド、
#     free_chat を分岐する。
#   - ThreadWBS は「推論前コンテキスト（参照専用）」としてのみ利用する。
#   - task_create 時にのみ CDC 候補を 1 件生成して返す（固定キー: cdc_candidate）。
#
# RESPONSIBILITY TAGS:
#   [DISPATCH]      command_type に応じたハンドラ分岐
#   [CTX_READ]      ThreadWBS（参照専用）の要約・表示補助（編集禁止）
#   [TASK_META]     task_name / task_summary 等の Task 用メタ構築
#   [CDC_OUTPUT]    cdc_candidate の生成（task_create のみ）
#   [USER_MSG]      Discord へ返す message_for_user の生成（短文・安定）
#
# CONSTRAINTS (HARD):
#   - 外部 I/O（DB / Notion / Discord）は一切行わない（純ロジック層）。
#   - ThreadWBS の更新・保存・候補確定は禁止（IFACE が担当）。
#   - 戻り値は dict のみ。I/O は上位レイヤ（BIS / Stabilizer）で処理する。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional, List


# ============================================================
# Public Entry
# ============================================================

def run_core(core_input: Dict[str, Any]) -> Dict[str, Any]:
    command_type = core_input.get("command_type", "free_chat")
    raw_text = core_input.get("raw_text", "") or ""
    arg_text = core_input.get("arg_text", "") or ""
    task_id = core_input.get("task_id")
    context_key = core_input.get("context_key")
    user_id = core_input.get("user_id")

    # STEP B: ThreadWBS (read-only context)
    thread_wbs = core_input.get("thread_wbs")

    # task_id がまだない場合は context_key を fallback
    if task_id is None and context_key is not None:
        task_id = str(context_key)

    # ----------------------------
    # Task lifecycle
    # ----------------------------
    if command_type == "task_create":
        return _handle_task_create(task_id, arg_text, user_id, thread_wbs)

    if command_type == "task_start":
        return _handle_task_start(task_id, arg_text, thread_wbs)

    if command_type == "task_paused":
        return _handle_task_paused(task_id, thread_wbs)

    if command_type == "task_end":
        return _handle_task_end(task_id, thread_wbs)

    # ----------------------------
    # ThreadWBS explicit commands
    #   - IFACE が更新・表示整形を担当するため、
    #     Core は過剰に話さない（安定優先）
    # ----------------------------
    if command_type in {"wbs_show", "wbs_accept", "wbs_reject", "wbs_edit"}:
        return _handle_wbs_command(command_type, task_id, thread_wbs)

    # ----------------------------
    # Fallback
    # ----------------------------
    return _handle_free_chat(raw_text, user_id, context_key, thread_wbs)


# ============================================================
# ThreadWBS helpers (read-only)
# ============================================================

def _as_dict(v: Any) -> Optional[Dict[str, Any]]:
    return v if isinstance(v, dict) else None


def _wbs_brief(thread_wbs: Any) -> str:
    """
    ThreadWBS を参照専用で短く要約する。
    失敗しても空文字で返す（Core安定優先）。
    """
    wbs = _as_dict(thread_wbs)
    if not wbs:
        return ""

    task = (wbs.get("task") or "").strip()
    status = (wbs.get("status") or "").strip()
    focus = wbs.get("focus_point", None)
    items = wbs.get("work_items") or []

    focus_line = ""
    try:
        if isinstance(focus, int) and isinstance(items, list) and 0 <= focus < len(items):
            it = items[focus]
            if isinstance(it, dict):
                r = (it.get("rationale") or "").strip()
                if r:
                    focus_line = f"- focus: [{focus}] {r}"
    except Exception:
        focus_line = ""

    lines: List[str] = []
    if task:
        lines.append(f"- wbs_task: {task}")
    if status:
        lines.append(f"- wbs_status: {status}")
    if focus_line:
        lines.append(focus_line)

    return "\n".join(lines).strip()


# ============================================================
# Handlers
# ============================================================

def _handle_task_create(
    task_id: str | None,
    arg_text: str,
    user_id: str | None,
    thread_wbs: Any,
) -> Dict[str, Any]:
    """
    新規タスク登録。
    - task_create 時のみ CDC 候補を 1 件生成する（固定キー: cdc_candidate）。
    - ThreadWBS は参照のみ（メッセージ補助）。
    """
    if task_id is None:
        return {
            "message_for_user": "[task_create] このコマンドはスレッド内でのみ有効です。",
            "mode": "free_chat",
        }

    title = arg_text.strip() or f"Task {task_id}"
    user_label = user_id or "unknown"

    wbs_hint = _wbs_brief(thread_wbs)
    wbs_block = f"\n\n[CTX]\n{wbs_hint}" if wbs_hint else ""

    msg = (
        "[task_create] 新しいタスクを登録しました。\n"
        f"- task_id   : {task_id}\n"
        f"- name      : {title}\n"
        f"- created_by: {user_label}\n"
        f"{wbs_block}\n\n"
        "[CDC] 作業候補を生成しました。承認: !wy / 破棄: !wn / 編集採用: !we"
    ).strip()

    # CDC Candidate（固定キー・1行）
    cdc_candidate = {
        "rationale": f"{title} の最初の作業項目を定義する"
    }

    return {
        "message_for_user": msg,
        "mode": "task_create",
        "task_name": title,
        "task_id": task_id,
        "cdc_candidate": cdc_candidate,
    }


def _handle_task_start(task_id: str | None, arg_text: str, thread_wbs: Any) -> Dict[str, Any]:
    """
    学習セッション開始。
    """
    if task_id is None:
        return {
            "message_for_user": "[task_start] スレッド内で実行してください。",
            "mode": "free_chat",
        }

    memo = arg_text.strip()
    memo_line = f"- memo   : {memo}\n" if memo else ""

    wbs_hint = _wbs_brief(thread_wbs)
    wbs_block = f"\n\n[CTX]\n{wbs_hint}" if wbs_hint else ""

    msg = (
        "[task_start] 学習セッションを開始しました。\n"
        f"- task_id: {task_id}\n"
        f"{memo_line}"
        "※ task_end までの時間が duration に記録されます。"
        f"{wbs_block}"
    ).strip()

    return {
        "message_for_user": msg,
        "mode": "task_start",
        "task_id": task_id,
        "memo": memo,
    }


def _handle_task_paused(task_id: str | None, thread_wbs: Any) -> Dict[str, Any]:
    """
    学習一時停止。
    - TaskSummary A案(min): message をそのまま task_summary として返す（現行方針）
    """
    if task_id is None:
        return {
            "message_for_user": "[task_paused] スレッド内で実行してください。",
            "mode": "free_chat",
        }

    wbs_hint = _wbs_brief(thread_wbs)
    wbs_block = f"\n\n[CTX]\n{wbs_hint}" if wbs_hint else ""

    msg = (
        "[task_paused] 学習を一時停止しました。\n"
        f"- task_id: {task_id}"
        f"{wbs_block}"
    ).strip()

    return {
        "message_for_user": msg,
        "mode": "task_paused",
        "task_id": task_id,
        "task_summary": msg,
    }


def _handle_task_end(task_id: str | None, thread_wbs: Any) -> Dict[str, Any]:
    """
    学習セッション終了。
    - TaskSummary A案(min): message をそのまま task_summary として返す（現行方針）
    """
    if task_id is None:
        return {
            "message_for_user": "[task_end] スレッド内で実行してください。",
            "mode": "free_chat",
        }

    wbs_hint = _wbs_brief(thread_wbs)
    wbs_block = f"\n\n[CTX]\n{wbs_hint}" if wbs_hint else ""

    msg = (
        "[task_end] 学習セッションを終了しました。\n"
        f"- task_id: {task_id}"
        f"{wbs_block}"
    ).strip()

    return {
        "message_for_user": msg,
        "mode": "task_end",
        "task_id": task_id,
        "task_summary": msg,
    }


def _handle_wbs_command(command_type: str, task_id: str | None, thread_wbs: Any) -> Dict[str, Any]:
    """
    WBS系明示コマンド。
    - IFACE が保存/表示/候補確定を担うため、Core は最小応答に留める。
    - ただし mode は必ず返す（Stabilizer の分岐安定化）。
    """
    if task_id is None:
        return {
            "message_for_user": "[wbs] スレッド内で実行してください。",
            "mode": "free_chat",
        }

    # IFACE が user_hint を追記する前提。Core は短い固定文のみ。
    msg = f"[{command_type}] routed."

    # 表示コマンドだけは、WBSが存在するなら軽く文脈を返す（重複は許容）
    if command_type == "wbs_show":
        wbs_hint = _wbs_brief(thread_wbs)
        if wbs_hint:
            msg = f"[wbs_show] current context:\n{wbs_hint}"

    return {
        "message_for_user": msg,
        "mode": command_type,
        "task_id": task_id,
    }


def _handle_free_chat(
    raw_text: str,
    user_id: str | None,
    context_key: str | None,
    thread_wbs: Any,
) -> Dict[str, Any]:
    """
    Task 管理コマンド以外の Fallback。
    - 現フェーズでは「安定した短文 + WBS文脈（参照のみ）」に留める。
    """
    base = raw_text.strip() or "(empty)"
    wbs_hint = _wbs_brief(thread_wbs)
    wbs_block = f"\n\n[CTX]\n{wbs_hint}" if wbs_hint else ""

    msg = (
        "[free_chat] タスク管理モード（Persist / Notion 連携）を優先しています。\n"
        f"- user_id    : {user_id or 'unknown'}\n"
        f"- context_key: {context_key or 'none'}"
        f"{wbs_block}\n\n"
        "---- Echo ----\n"
        f"{base}"
    ).strip()

    return {
        "message_for_user": msg,
        "mode": "free_chat",
    }