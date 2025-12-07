# ovv/brain/threadbrain_adapter.py
# ThreadBrainAdapter v3.2 – TB 正規化 + semantic-cleaning
#
# [MODULE CONTRACT]
# NAME: threadbrain_adapter
# ROLE: ThreadBrainAdapter_v3.2
#
# INPUT:
#   summary: dict | None   # v1/v2/v3 いずれかの ThreadBrain summary
#
# OUTPUT:
#   normalized_summary: dict | None   # TB v3.2 正規形（semantic-clean 済み）
#   tb_prompt: str                    # [TB] セクション付き LLM 用プロンプト
#
# MUST:
#   - upgrade(TB_v1_v2_to_v3)
#   - keep(constraints_soft_only)
#   - drop(format_hard_constraints)
#   - clean(decisions/unresolved/next_actions/recent_messages/history_digest/current_position
#           from hard output instructions)
#   - preserve_core_fields (status / high_level_goal / history_digest 等の意味は維持)
#   - be_deterministic
#
# MUST NOT:
#   - call_LLM
#   - perform_IO (DB / Discord / Notion)
#   - depend_on(Boundary_Gate / Interface_Box / ovv_call)
#   - alter_user_intent

from typing import Optional, Dict, Any, List

from ovv.brain.constraint_classifier import classify_constraint_text


# ============================================================
# 内部ヘルパ: 旧 constraints 抽出（v1/v2 用）
# ============================================================

def _extract_constraint_text(item: Any) -> str:
    """
    v1 互換用:
      - 文字列 → そのまま
      - dict {"text": "..."} → text
      - それ以外 → 空文字
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("text"), str):
        return item["text"]
    return ""


def _split_constraints_v1(constraints: List[Any]) -> Dict[str, List[str]]:
    """
    v1/v2 の constraints 配列を soft/hard に振り分ける。
    - hard は破棄対象（TB v3 では保持しない）
    - unknown は安全側で soft に寄せる
    """
    soft: List[str] = []
    hard: List[str] = []

    for item in constraints:
        text = _extract_constraint_text(item)
        if not text:
            continue

        cls = classify_constraint_text(text)
        if cls == "hard":
            # hard は TB v3 では保持しない（後続で破棄）
            hard.append(text)
        else:
            # soft / unknown は soft 側に寄せる（unknown はユーザー意図を尊重）
            soft.append(text)

    return {"soft": soft, "hard": hard}


# ============================================================
# TB v3 正規化（形式アップグレード）
# ============================================================

def _upgrade_to_v3(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    v1/v2 形式の ThreadBrain summary を v3 形式に正規化する。
    - constraints → constraints_soft のみ
    - constraints_hard 相当は生成しても保持しない（破棄）
    """
    if not isinstance(summary, dict):
        return {
            "meta": {
                "version": "3.0",
            },
            "status": {},
            "decisions": [],
            "unresolved": [],
            "constraints_soft": [],
            "next_actions": [],
            "history_digest": "",
            "high_level_goal": "",
            "recent_messages": [],
            "current_position": "",
        }

    src_meta = summary.get("meta") or {}
    src_status = summary.get("status") or {}
    src_decisions = summary.get("decisions") or []
    src_unresolved = summary.get("unresolved") or []
    src_next = summary.get("next_actions") or []
    src_history = summary.get("history_digest") or ""
    src_goal = summary.get("high_level_goal") or ""
    src_recent = summary.get("recent_messages") or []
    src_pos = summary.get("current_position") or ""

    # 旧 constraints を取得（v1/v2 互換）
    raw_constraints: List[Any] = []
    if isinstance(summary.get("constraints"), list):
        raw_constraints = summary["constraints"]
    elif isinstance(summary.get("thread_brain"), dict):
        tb_inner = summary["thread_brain"]
        if isinstance(tb_inner.get("constraints"), list):
            raw_constraints = tb_inner["constraints"]

    split = _split_constraints_v1(raw_constraints)
    soft_constraints = split["soft"]
    # hard_constraints = split["hard"]  # TB v3 では保持しない

    v3: Dict[str, Any] = {
        "meta": {
            "version": "3.0",
            "updated_at": src_meta.get("updated_at"),
            "context_key": src_meta.get("context_key"),
            "total_tokens_estimate": src_meta.get("total_tokens_estimate"),
        },
        "status": src_status,
        "decisions": list(src_decisions),
        "unresolved": list(src_unresolved),
        "constraints_soft": soft_constraints,
        "next_actions": list(src_next),
        "history_digest": src_history,
        "high_level_goal": src_goal,
        "recent_messages": list(src_recent),
        "current_position": src_pos,
    }

    return v3


# ============================================================
# semantic-cleaning ヘルパ
# ============================================================

def _clean_list_field(items: Any) -> List[str]:
    """
    decisions / unresolved / next_actions / recent_messages / constraints_soft
    のような「テキスト配列」から、出力形式 hard 指示を除去する。
    """
    if not isinstance(items, list):
        return []

    cleaned: List[str] = []
    for raw in items:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        cls = classify_constraint_text(text)
        if cls == "hard":
            # 例:
            # - JSON 形式でのみ返すこと
            # - マークダウン禁止
            # - 構造化データのみ返す
            # → TB 長期記憶には残さない
            continue
        cleaned.append(text)
    return cleaned


def _split_into_sentences(text: str) -> List[str]:
    """
    ごく簡易な日本語/英語混在向け sentence split。
    句点・改行単位で区切る。
    """
    if not text:
        return []

    segments: List[str] = []

    for line in str(text).splitlines():
        buf = ""
        for ch in line:
            buf += ch
            if ch in ("。", "！", "？", ".", "!", "?"):
                if buf.strip():
                    segments.append(buf)
                buf = ""
        if buf.strip():
            segments.append(buf)

    return segments


def _join_sentences(segments: List[str]) -> str:
    """
    sentence list を 1 つの paragraph に戻す。
    ここでは単純にスペース区切りで連結する。
    """
    segs = [s.strip() for s in segments if s and s.strip()]
    if not segs:
        return ""
    return " ".join(segs)


def _clean_paragraph(text: Any) -> str:
    """
    history_digest / current_position のような「要約文」から、
    出力形式 hard 指示を含む文を削除する。
    """
    if text is None:
        return ""
    raw = str(text)
    if not raw.strip():
        return ""

    segments = _split_into_sentences(raw)
    kept: List[str] = []

    for seg in segments:
        s = seg.strip()
        if not s:
            continue
        cls = classify_constraint_text(s)
        if cls == "hard":
            # 「このスレッドでは JSON 形式でのみ返答する」等を落とす
            continue
        kept.append(s)

    return _join_sentences(kept)


# ============================================================
# Public API: normalize_thread_brain
# ============================================================

def normalize_thread_brain(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Public API:
      - v1/v2/v3 いずれの TB でも受け取り、
        v3.2 形式（constraints_soft のみ + semantic-clean 済み）に正規化して返す。
      - None の場合は None を返す。
    """
    if summary is None:
        return None

    base: Dict[str, Any]
    if not isinstance(summary, dict):
        base = {}
    else:
        base = summary

    meta = base.get("meta") or {}
    version = str(meta.get("version", "")).strip()

    # 1) v1/v2 → v3 にアップグレード
    if version.startswith("3"):
        # すでに v3 想定。最低限のフィールドがあるかだけ確認して shallow copy。
        tb_v3: Dict[str, Any] = dict(base)
        if not isinstance(tb_v3.get("constraints_soft"), list):
            tb_v3["constraints_soft"] = []
    else:
        tb_v3 = _upgrade_to_v3(base)

    # 2) semantic-cleaning 適用
    # 2-1) constraints_soft 再スキャン（念のため）
    tb_v3["constraints_soft"] = _clean_list_field(tb_v3.get("constraints_soft"))

    # 2-2) list 系フィールドをクリーン
    for key in ["decisions", "unresolved", "next_actions", "recent_messages"]:
        tb_v3[key] = _clean_list_field(tb_v3.get(key))

    # 2-3) paragraph 系フィールドをクリーン
    tb_v3["history_digest"] = _clean_paragraph(tb_v3.get("history_digest", ""))
    tb_v3["current_position"] = _clean_paragraph(tb_v3.get("current_position", ""))

    # 3) meta.version を v3.2 に揃える（明示）
    meta_out = tb_v3.get("meta") or {}
    meta_out = dict(meta_out)
    meta_out["version"] = "3.2"
    tb_v3["meta"] = meta_out

    return tb_v3


# ============================================================
# TB Prompt Builder
# ============================================================

def build_tb_prompt(thread_brain: Optional[Dict[str, Any]]) -> str:
    """
    TB v3.2 を前提に、LLM へ渡す [TB] プロンプトを構築する。
    - None の場合は最小限のプレースホルダを返す。
    - v1/v2 の場合も normalize_thread_brain で v3.2 に揃える。
    """

    tb_v3 = normalize_thread_brain(thread_brain)
    if tb_v3 is None:
        return "[TB]\nNo thread brain available."

    meta = tb_v3.get("meta") or {}
    status = tb_v3.get("status") or {}
    decisions = tb_v3.get("decisions") or []
    unresolved = tb_v3.get("unresolved") or []
    constraints_soft = tb_v3.get("constraints_soft") or []
    next_actions = tb_v3.get("next_actions") or []
    history_digest = tb_v3.get("history_digest") or ""
    high_level_goal = tb_v3.get("high_level_goal") or ""
    recent_messages = tb_v3.get("recent_messages") or []
    current_position = tb_v3.get("current_position") or ""

    lines: List[str] = []
    lines.append("[TB]")
    lines.append(f"version: {meta.get('version', '3.2')}")
    if meta.get("context_key") is not None:
        lines.append(f"context_key: {meta.get('context_key')}")

    # High Level Goal
    if high_level_goal:
        lines.append("\n[GOAL]")
        lines.append(high_level_goal)

    # Status
    if status:
        lines.append("\n[STATUS]")
        phase = status.get("phase")
        if phase:
            lines.append(f"- phase: {phase}")
        last_event = status.get("last_major_event")
        if last_event:
            lines.append(f"- last_major_event: {last_event}")

    # Decisions
    if decisions:
        lines.append("\n[DECISIONS]")
        for d in decisions:
            lines.append(f"- {d}")

    # Unresolved
    if unresolved:
        lines.append("\n[UNRESOLVED]")
        for u in unresolved:
            lines.append(f"- {u}")

    # Soft Constraints（会話ルール）
    if constraints_soft:
        lines.append("\n[CONSTRAINTS_SOFT]")
        for c in constraints_soft:
            lines.append(f"- {c}")

    # Next Actions
    if next_actions:
        lines.append("\n[NEXT_ACTIONS]")
        for n in next_actions:
            lines.append(f"- {n}")

    # History Digest
    if history_digest:
        lines.append("\n[HISTORY_DIGEST]")
        lines.append(history_digest)

    # Current Position
    if current_position:
        lines.append("\n[CURRENT_POSITION]")
        lines.append(current_position)

    # Recent Messages（必要なら短く）
    if recent_messages:
        lines.append("\n[RECENT_MESSAGES]")
        for msg in recent_messages[-5:]:
            lines.append(f"- {msg}")

    return "\n".join(lines)