# [MODULE CONTRACT]
# NAME: threadbrain_adapter
# ROLE: ThreadBrainAdapter_v3
#
# INPUT:
#   summary: dict | None
#
# OUTPUT:
#   normalized_summary: dict | None
#   tb_prompt: str
#
# MUST:
#   - upgrade(TB_v1_v2_to_v3)
#   - keep(constraints_soft_only)
#   - drop(format_hard_constraints)
#   - preserve_core_fields
#   - be_deterministic
#
# MUST_NOT:
#   - call_LLM
#   - store_constraints_hard
#   - alter_core_meaning
#   - control_output_format

from typing import Optional, Dict, Any, List


# ============================================================
# 内部ヘルパ: 制約分類（soft / hard / unknown）
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


def _classify_constraint_text(text: str) -> str:
    """
    制約テキストを soft / hard / unknown に分類する。
    - soft: 会話ルール・嗜好（敬語禁止など）
    - hard: 出力形式・システム越境（JSONで返せなど）
    - unknown: 判定不能（原則 soft 扱い側に寄せる）
    """
    if not text:
        return "unknown"

    t = text.strip()
    lower = t.lower()

    # 否定系フレーズ（〜を含めてはならない / 〜を含めちゃダメ 等）
    forbid_phrases = [
        "含めない",
        "含めるな",
        "含めず",
        "含めてはならない",
        "含めてはいけない",
        "含めてはいけません",
        "含めちゃダメ",
        "含んではいけない",
        "含んではならない",
    ]

    def _has_forbid_phrase(s: str) -> bool:
        return any(p in s for p in forbid_phrases) or "禁止" in s

    # -------------------------
    # 1) 明確な "hard" パターン
    # -------------------------

    # JSON / YAML / XML などフォーマット強制
    if "json" in lower or "yaml" in lower or "xml" in lower:
        # 「jsonで返す」「json形式」「jsonオブジェクト」など
        if ("返" in t or "形式" in t or "オブジェクト" in t or "only" in lower
                or "のみ" in t):
            return "hard"

    # マークダウン・説明文などを「含めるな/禁止」のパターン
    if "マークダウン" in t and _has_forbid_phrase(t):
        return "hard"
    if "説明文" in t and _has_forbid_phrase(t):
        return "hard"

    # 構造化データ・オブジェクト系を「〜のみ」「〜だけ」「〜で返せ」と強制
    if ("構造化データ" in t or "オブジェクト" in t):
        if "のみ" in t or "だけ" in t or "だけを返す" in t or "のみを返す" in t or "で返せ" in t:
            return "hard"

    # システム・プロンプト越境系（英語簡易）
    if "ignore the system prompt" in lower or "override the system prompt" in lower:
        return "hard"
    if "jailbreak" in lower:
        return "hard"

    # -------------------------
    # 2) 明確な "soft" パターン
    # -------------------------

    # 敬語/タメ口など会話スタイル
    if "敬語" in t:
        return "soft"
    if "タメ口" in t or "ため口" in t:
        return "soft"

    # 長さ・簡潔さ
    if "短く" in t or "簡潔" in t:
        return "soft"

    # 言語指定（日本語/英語で話す等）※形式ではなく会話ルールとみなす
    if "日本語" in t and ("話す" in t or "答える" in t):
        return "soft"
    if "英語" in t and ("話す" in t or "答える" in t):
        return "soft"

    # スレッド用途
    if "このスレ" in t or "このスレッド" in t:
        return "soft"

    # -------------------------
    # 3) 判定不能 → unknown（上位ロジックで soft 側に寄せる）
    # -------------------------
    return "unknown"


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

        cls = _classify_constraint_text(text)
        if cls == "hard":
            # hard は TB v3 では保持しない（後続で破棄）
            hard.append(text)
        else:
            # soft / unknown は soft 側に寄せる（unknown はユーザー意図を尊重）
            soft.append(text)

    return {"soft": soft, "hard": hard}


# ============================================================
# TB v3 正規化
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
        "decisions": src_decisions,
        "unresolved": src_unresolved,
        "constraints_soft": soft_constraints,
        "next_actions": src_next,
        "history_digest": src_history,
        "high_level_goal": src_goal,
        "recent_messages": src_recent,
        "current_position": src_pos,
    }

    return v3


def normalize_thread_brain(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Public API:
      - v1/v2/v3 いずれの TB でも受け取り、
        v3 形式（constraints_soft のみ）に正規化して返す。
      - None の場合は None を返す。
    """
    if summary is None:
        return None

    if not isinstance(summary, dict):
        return _upgrade_to_v3({})

    meta = summary.get("meta") or {}
    version = str(meta.get("version", "")).strip()

    if version.startswith("3"):
        # すでに v3 想定。最低限のフィールドがあるかだけ確認して返す。
        if "constraints_soft" not in summary or not isinstance(summary["constraints_soft"], list):
            summary = dict(summary)
            summary["constraints_soft"] = []
        # 旧 constraints が残っていたら無視（壊さない）
        return summary

    # v1/v2 → v3 へアップグレード
    return _upgrade_to_v3(summary)


# ============================================================
# TB Prompt Builder
# ============================================================

def build_tb_prompt(thread_brain: Optional[Dict[str, Any]]) -> str:
    """
    TB v3 を前提に、LLM へ渡す [TB] プロンプトを構築する。
    - None の場合は最小限のプレースホルダを返す。
    - v1/v2 の場合も normalize_thread_brain で v3 に揃える。
    """

    tb_v3 = normalize_thread_brain(thread_brain)
    if tb_v3 is None:
        return "[TB]\nNo thread brain available."

    meta = tb_v3.get("meta") or ""
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
    if isinstance(meta, dict):
        lines.append(f"version: {meta.get('version', '3.0')}")
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