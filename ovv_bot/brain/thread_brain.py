# ovv/brain/threadbrain_adapter.py
#
# [MODULE CONTRACT]
# NAME: threadbrain_adapter
# ROLE: ThreadBrainAdapter_v3_1
#
# INPUT:
#   summary: dict | None
#
# OUTPUT:
#   - normalize_thread_brain(summary) -> dict | None
#   - build_tb_prompt(summary) -> str
#
# MUST:
#   - normalize(v1/v2 -> v3.1)
#   - preserve(long_term_structure)
#   - drop(short_term_noise)
#   - limit(size <= ~5000 chars)
#   - be_deterministic
#
# MUST_NOT:
#   - call_LLM
#   - perform_IO
#   - depend_on(Discord/PG/Notion)
#   - store(hard_constraints)
#
# BOUNDARY:
#   - このモジュールは「TB 正規化」と「TB 用プロンプト生成」のみ担当する。
#   - constraint_filter / interface_box / ovv_call からのみ参照される。
#   - BIS の他レイヤ（Boundary_Gate / Stabilizer / Storage）には依存しない。

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ============================================================
# 内部ユーティリティ
# ============================================================

def _ensure_dict(obj: Any) -> Optional[Dict[str, Any]]:
    """summary が dict でなければ None を返す。安全側に倒す。"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    # 将来的に string JSON などを扱いたければここで parse するが、
    # 現時点では「異常データは無視」の方針で None を返す。
    return None


def _to_str_list(value: Any) -> List[str]:
    """
    汎用 → List[str] 正規化。
    - None      → []
    - str       → [str]
    - list[...] → 文字列に変換してフィルタ
    """
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, list):
        out: List[str] = []
        for v in value:
            if v is None:
                continue
            s = str(v).strip()
            if s:
                out.append(s)
        return out
    # その他型は捨てる
    return []


def _truncate_string(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    # 末尾に truncated マーカーを足す（ただし全体長は max_chars 以下）
    marker = " …[truncated]"
    if max_chars <= len(marker):
        return s[:max_chars]
    body_len = max_chars - len(marker)
    return s[:body_len] + marker


def _truncate_list(items: List[str], max_items: int) -> List[str]:
    """
    リストの最大長制御。
    - max_items 以内ならそのまま
    - 超えたら、先頭 max_items-1 件 + 「(+N older items)」1件にまとめる
    """
    if len(items) <= max_items:
        return items
    keep = items[: max_items - 1]
    rest = len(items) - (max_items - 1)
    keep.append(f"...(+{rest} older items)")
    return keep


def _is_numeric_like(text: str) -> bool:
    """
    数字カウントや数値のみの短文かどうかの簡易判定。
    - 完全に数字だけ
    - 数字 + 空白
    """
    t = text.strip()
    if not t:
        return False
    # コマンドやタグなら除外
    if t.startswith("!"):
        return False
    # 純数字 or 数字と空白だけ
    return all(ch.isdigit() or ch.isspace() for ch in t)


def _is_low_value_recent_message(text: str) -> bool:
    """
    TB に残す価値の低い recent_messages 判定。
    - 数字だけ
    - ごく短い一言（2〜3文字）で構造情報を持たないもの
    - デバッグっぽいラベル
    """
    t = text.strip()
    if not t:
        return True
    if _is_numeric_like(t):
        return True
    if t.startswith("[DBG]"):
        return True
    # かなり短く、記号や相槌っぽいものは TB から外す
    if len(t) <= 3:
        # 例: 「はい」「OK」「うん」などは runtime_memory に任せる
        return True
    return False


def _filter_recent_messages(raw_msgs: Any, max_items: int = 5) -> List[str]:
    """
    recent_messages を「TB v3.1 に残すべきメッセージ」だけに絞る。
    - 数字・コマンド・極短文は TB から除外。
    - 新しい方から拾って最大 max_items 件。
    """
    msgs = _to_str_list(raw_msgs)
    if not msgs:
        return []

    picked: List[str] = []
    # 新しいメッセージがリスト末尾想定なので逆順で走査
    for m in reversed(msgs):
        if _is_low_value_recent_message(m):
            continue
        picked.append(m)
        if len(picked) >= max_items:
            break

    picked.reverse()
    return picked


# ============================================================
# normalize_thread_brain（TB v3.1 正規化 & 圧縮）
# ============================================================

def normalize_thread_brain(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Thread Brain summary を TB v3.1 正規形にそろえる。

    役割:
      - v1/v2 形式が来た場合:
          → v3.1 の標準フィールド構造にマッピングする。
      - すでに v3/v3.1 の場合:
          → サイズ制御・粒度制御のみ行う。
      - 短期ログ（数字カウント・コマンド・極短文）は TB から排除する。

    ポリシー:
      - LLM は呼ばない（要約が必要な場合も deterministic に truncate）。
      - hard constraints や JSON-only 指示などはここでは扱わない
        （constraints_hard は生成側で TB に残さない前提）。
    """
    src = _ensure_dict(summary)
    if src is None:
        return None

    # ---- 基本フィールドの取り出し・正規化 ----
    meta = _ensure_dict(src.get("meta")) or {}
    status = _ensure_dict(src.get("status")) or {}

    # meta.version を TB v3.1 として上書きしつつ、他の情報は保持
    meta = dict(meta)
    meta["version"] = "3.1"

    # decisions / constraints / constraints_soft / next_actions
    decisions = _to_str_list(src.get("decisions"))
    constraints_soft = _to_str_list(src.get("constraints_soft"))
    # 旧フィールド名 "constraints" があれば constraints_soft にマージ
    legacy_constraints = _to_str_list(src.get("constraints"))
    if legacy_constraints:
        constraints_soft = constraints_soft + legacy_constraints

    next_actions = _to_str_list(src.get("next_actions"))

    # history_digest / high_level_goal / current_position
    history_digest = str(src.get("history_digest") or "").strip()
    high_level_goal = str(src.get("high_level_goal") or "").strip()
    current_position = str(src.get("current_position") or "").strip()

    unresolved = _to_str_list(src.get("unresolved"))

    # recent_messages（数字や極短文は TB から外す / 最大5件）
    recent_messages = _filter_recent_messages(src.get("recent_messages"), max_items=5)

    # ---- 粒度・サイズ圧縮（List 系）----
    decisions = _truncate_list(decisions, max_items=15)
    constraints_soft = _truncate_list(constraints_soft, max_items=10)
    next_actions = _truncate_list(next_actions, max_items=8)
    unresolved = _truncate_list(unresolved, max_items=10)

    # ---- 粒度・サイズ圧縮（String 系）----
    # history_digest は長期サマリなので 1200 文字程度を上限にする
    history_digest = _truncate_string(history_digest, max_chars=1200)
    # high_level_goal / current_position は比較的短い想定だが一応制限
    high_level_goal = _truncate_string(high_level_goal, max_chars=200)
    current_position = _truncate_string(current_position, max_chars=400)

    # ---- 正規化結果の構築 ----
    normalized: Dict[str, Any] = {
        "meta": meta,
        "status": status,
        "decisions": decisions,
        "constraints_soft": constraints_soft,
        "next_actions": next_actions,
        "history_digest": history_digest,
        "high_level_goal": high_level_goal,
        "unresolved": unresolved,
        "recent_messages": recent_messages,
        "current_position": current_position,
    }

    # 余剰フィールド（例えば custom なメタ情報）があれば最低限引き継ぐが、
    # TB の意味を壊さないようにする。
    # 例: status 内の risk / phase / last_major_event などは status に残してある。

    return normalized


# ============================================================
# build_tb_prompt（TB v3.1 → System 用プロンプト）
# ============================================================

def build_tb_prompt(summary: Optional[Dict[str, Any]]) -> str:
    """
    TB v3.1 形式の summary から、Ovv Core に渡す Thread Brain プロンプトを生成する。
    - 長期方針 / 決定事項 / 制約 / 次アクション / 状態サマリ をコンパクトに並べる。
    - runtime_memory と重複するような短期ログは含めない前提。
    """
    tb = normalize_thread_brain(summary)
    if not tb:
        return ""

    meta = tb.get("meta") or {}
    status = tb.get("status") or {}

    high_level_goal = tb.get("high_level_goal") or ""
    decisions = tb.get("decisions") or []
    constraints_soft = tb.get("constraints_soft") or []
    next_actions = tb.get("next_actions") or []
    history_digest = tb.get("history_digest") or ""
    current_position = tb.get("current_position") or ""
    unresolved = tb.get("unresolved") or []

    lines: List[str] = []
    lines.append("[THREAD_BRAIN v3.1]")

    # Meta
    if high_level_goal:
        lines.append("")
        lines.append("[HIGH_LEVEL_GOAL]")
        lines.append(high_level_goal)

    # Status
    phase = ""
    if isinstance(status, dict):
        phase = status.get("phase") or ""
    if phase:
        lines.append("")
        lines.append("[STATUS]")
        lines.append(f"phase: {phase}")

    # Decisions
    if decisions:
        lines.append("")
        lines.append("[DECISIONS]")
        for d in decisions:
            lines.append(f"- {d}")

    # Constraints (soft)
    if constraints_soft:
        lines.append("")
        lines.append("[CONSTRAINTS_SOFT]")
        for c in constraints_soft:
            lines.append(f"- {c}")

    # Next Actions
    if next_actions:
        lines.append("")
        lines.append("[NEXT_ACTIONS]")
        for n in next_actions:
            lines.append(f"- {n}")

    # Unresolved
    if unresolved:
        lines.append("")
        lines.append("[UNRESOLVED]")
        for u in unresolved:
            lines.append(f"- {u}")

    # History Digest
    if history_digest:
        lines.append("")
        lines.append("[HISTORY_DIGEST]")
        lines.append(history_digest)

    # Current Position
    if current_position:
        lines.append("")
        lines.append("[CURRENT_POSITION]")
        lines.append(current_position)

    # ここで長さが極端に伸びる場合があるが、TB 側で既に 3〜5k 文字に制御されている前提。
    text = "\n".join(lines).strip()
    return text