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
#   - compress(excessive_recent_messages)
#   - cap(long_text_fields)
#
# MUST_NOT:
#   - call_LLM
#   - store_constraints_hard
#   - alter_core_meaning
#   - control_output_format

from typing import Optional, Dict, Any, List

# ============================================================
# v3.1 Tunable Limits
# ============================================================

MAX_DECISIONS = 12
MAX_UNRESOLVED = 12
MAX_NEXT_ACTIONS = 12

MAX_DECISION_LEN = 300
MAX_UNRESOLVED_LEN = 300
MAX_NEXT_ACTION_LEN = 300

MAX_HISTORY_LEN = 1200
MAX_GOAL_LEN = 600
MAX_POSITION_LEN = 400

MAX_RECENT_MESSAGES = 16
RECENT_TAIL_KEEP = 6  # 直近は必ず残す


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
    - hard: 出力形式・システム越境（JSONで返せ等）
    - unknown: 判定不能（原則 soft 扱い側に寄せる）
    """
    if not text:
        return "unknown"

    t = text.strip()
    lower = t.lower()

    # -------------------------
    # 1) 明確な "hard" パターン
    # -------------------------

    # JSON / YAML / XML などフォーマット強制
    if "json" in lower or "yaml" in lower or "xml" in lower:
        if "返" in t or "形式" in t or "オブジェクト" in t or "only" in lower:
            return "hard"

    # マークダウン禁止・説明文禁止など
    if "マークダウン" in t and ("含めない" in t or "禁止" in t):
        return "hard"
    if "説明文" in t and "含めない" in t:
        return "hard"

    # 構造化データ強制
    if "構造化データ" in t and "返" in t:
        return "hard"
    if "オブジェクト" in t and "のみ" in t and "返" in t:
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
            hard.append(text)
        else:
            soft.append(text)

    return {"soft": soft, "hard": hard}


# ============================================================
# 汎用ユーティリティ（v3.1 圧縮用）
# ============================================================

def _truncate_str(s: str, max_len: int) -> str:
    s = str(s)
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    # 末尾にマーカーを入れて最大長に収める
    marker = "...[truncated]"
    if len(marker) >= max_len:
        return s[:max_len]
    head_len = max_len - len(marker)
    return s[:head_len] + marker


def _truncate_middle(s: str, max_len: int) -> str:
    """
    長い history_digest 用:
      - 先頭 + 末尾を残し、中間にマーカーを挿入
    """
    s = str(s)
    if max_len <= 0 or len(s) <= max_len:
        return s

    marker = "\n...[truncated]...\n"
    if len(marker) >= max_len:
        return s[:max_len]

    # だいたい 60:40 くらいで head/tail を分配
    head_len = int((max_len - len(marker)) * 0.6)
    tail_len = max_len - len(marker) - head_len
    if head_len <= 0 or tail_len <= 0:
        return s[:max_len]

    return s[:head_len] + marker + s[-tail_len:]


def _dedup_str_list(values: List[Any]) -> List[str]:
    """順序を保ったまま文字列リストを重複排除する。"""
    seen = set()
    out: List[str] = []
    for v in values:
        if v is None:
            continue
        s = str(v)
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _is_numeric_like(text: str) -> bool:
    """
    「ほぼ数字だけ」の低情報メッセージ判定。
    - 数字＋単純な記号のみ、かつ長さが短いものを低優先度とみなす。
    """
    t = text.strip()
    if not t:
        return False
    if len(t) > 8:
        return False
    allowed = set("0123456789+-*/×÷.,。、 　()[]")
    return all(ch in allowed for ch in t)


def _is_question_like(text: str) -> bool:
    """簡易な質問判定。state_manager と同レベルの軽量版。"""
    if not text:
        return False
    if "?" in text or "？" in text:
        return True
    q_keywords = ["教えて", "どう", "なに", "何", "どこ", "どれ", "なぜ", "なんで", "ですか", "でしょうか"]
    return any(k in text for k in q_keywords)


def _compress_recent_messages(messages: List[Any]) -> List[str]:
    """
    recent_messages を v3.1 ポリシーに従って圧縮する。
    - 最大 MAX_RECENT_MESSAGES 件
    - 直近 RECENT_TAIL_KEEP 件は必ず残す
    - それ以前はスコアベースで選択
    """
    # 文字列化＋空要素除去
    cleaned: List[str] = [str(m) for m in messages if m is not None and str(m).strip()]
    if len(cleaned) <= MAX_RECENT_MESSAGES:
        return cleaned

    total = len(cleaned)
    tail_start = max(total - RECENT_TAIL_KEEP, 0)
    tail_indices = set(range(tail_start, total))

    scored = []
    for idx, txt in enumerate(cleaned):
        score = 0
        if _is_question_like(txt):
            score += 2
        if len(txt) >= 20:
            score += 1
        if _is_numeric_like(txt):
            score -= 2
        scored.append((idx, score, txt))

    # 直近は無条件採用
    keep_indices = set(tail_indices)

    # 残り枠
    remaining_slots = MAX_RECENT_MESSAGES - len(keep_indices)
    if remaining_slots <= 0:
        # tail だけで埋まる場合
        selected = [cleaned[i] for i in sorted(keep_indices)]
        return selected

    # それ以前のログからスコア順で選ぶ
    candidates = [item for item in scored if item[0] not in keep_indices]
    # スコア降順、同点は新しいもの優先（idx 大きい方）
    candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)

    for idx, _score, _txt in candidates:
        keep_indices.add(idx)
        if len(keep_indices) >= MAX_RECENT_MESSAGES:
            break

    # 元の時間順で並べ直す
    selected = [cleaned[i] for i in sorted(keep_indices)]
    return selected


def _postprocess_v3(tb_v3: Dict[str, Any]) -> Dict[str, Any]:
    """
    v3 正規化後の追加圧縮・整形（v3.1）。
    - constraints_soft 重複排除
    - decisions / unresolved / next_actions の件数・長さ制限
    - history_digest / high_level_goal / current_position の長さ制限
    - recent_messages の圧縮
    """
    out = dict(tb_v3)  # 浅いコピー

    # constraints_soft
    soft_raw = out.get("constraints_soft") or []
    out["constraints_soft"] = _dedup_str_list(soft_raw)

    # decisions / unresolved / next_actions
    decisions = _dedup_str_list(out.get("decisions") or [])[:MAX_DECISIONS]
    decisions = [_truncate_str(d, MAX_DECISION_LEN) for d in decisions]
    out["decisions"] = decisions

    unresolved = _dedup_str_list(out.get("unresolved") or [])[:MAX_UNRESOLVED]
    unresolved = [_truncate_str(u, MAX_UNRESOLVED_LEN) for u in unresolved]
    out["unresolved"] = unresolved

    next_actions = _dedup_str_list(out.get("next_actions") or [])[:MAX_NEXT_ACTIONS]
    next_actions = [_truncate_str(n, MAX_NEXT_ACTION_LEN) for n in next_actions]
    out["next_actions"] = next_actions

    # history_digest / goal / current_position
    history = out.get("history_digest") or ""
    out["history_digest"] = _truncate_middle(str(history), MAX_HISTORY_LEN)

    goal = out.get("high_level_goal") or ""
    out["high_level_goal"] = _truncate_str(str(goal), MAX_GOAL_LEN)

    pos = out.get("current_position") or ""
    out["current_position"] = _truncate_str(str(pos), MAX_POSITION_LEN)

    # recent_messages 圧縮
    recent = out.get("recent_messages") or []
    out["recent_messages"] = _compress_recent_messages(recent)

    return out


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
        base = {
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
        return _postprocess_v3(base)

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

    return _postprocess_v3(v3)


def normalize_thread_brain(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Public API:
      - v1/v2/v3 いずれの TB でも受け取り、
        v3.1 形式（constraints_soft + 圧縮済み）に正規化して返す。
      - None の場合は None を返す。
    """
    if summary is None:
        return None

    if not isinstance(summary, dict):
        return _upgrade_to_v3({})

    meta = summary.get("meta") or {}
    version = str(meta.get("version", "")).strip()

    if version.startswith("3"):
        # すでに v3 想定。最低限のフィールドがあるかだけ確認し、v3.1 postprocess を適用。
        base = dict(summary)

        if "constraints_soft" not in base or not isinstance(base["constraints_soft"], list):
            base["constraints_soft"] = []

        if "decisions" not in base:
            base["decisions"] = []
        if "unresolved" not in base:
            base["unresolved"] = []
        if "next_actions" not in base:
            base["next_actions"] = []
        if "history_digest" not in base:
            base["history_digest"] = ""
        if "high_level_goal" not in base:
            base["high_level_goal"] = ""
        if "recent_messages" not in base:
            base["recent_messages"] = []
        if "current_position" not in base:
            base["current_position"] = ""

        return _postprocess_v3(base)

    # v1/v2 → v3.1 へアップグレード
    return _upgrade_to_v3(summary)


# ============================================================
# TB Prompt Builder
# ============================================================

def build_tb_prompt(thread_brain: Optional[Dict[str, Any]]) -> str:
    """
    TB v3.1 を前提に、LLM へ渡す [TB] プロンプトを構築する。
    - None の場合は最小限のプレースホルダを返す。
    - v1/v2 の場合も normalize_thread_brain で v3.1 に揃える。
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
    lines.append(f"version: {meta.get('version', '3.0')}")
    if meta.get("context_key") is not None:
        lines.append(f"context_key: {meta.get('context_key')}")

    # High Level Goal
    if high_level_goal:
        lines.append("\n[GOAL]")
        lines.append(str(high_level_goal))

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
        lines.append(str(history_digest))

    # Current Position
    if current_position:
        lines.append("\n[CURRENT_POSITION]")
        lines.append(str(current_position))

    # Recent Messages（直近圧縮済み）
    if recent_messages:
        lines.append("\n[RECENT_MESSAGES]")
        for msg in recent_messages:
            lines.append(f"- {msg}")

    return "\n".join(lines)