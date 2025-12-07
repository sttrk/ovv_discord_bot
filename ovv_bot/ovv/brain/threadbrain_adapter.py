# [MODULE CONTRACT]
# NAME: threadbrain_adapter
# ROLE: ThreadBrainAdapter_v3.1
#
# INPUT:
#   summary: dict | None
#
# OUTPUT:
#   normalized_summary: dict | None
#   tb_prompt: str
#
# MUST:
#   - upgrade(TB_v1_v2_to_v3_1)
#   - keep(constraints_soft_only)
#   - drop(format_hard_constraints)
#   - preserve_core_fields
#   - apply(granularity_control)
#   - be_deterministic
#
# MUST_NOT:
#   - call_LLM
#   - store_constraints_hard
#   - alter_core_meaning
#   - control_output_format

from typing import Optional, Dict, Any, List
from datetime import datetime, timezone


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

    # -------------------------
    # 1) 明確な "hard" パターン
    # -------------------------

    # JSON / YAML / XML などフォーマット強制
    if "json" in lower or "yaml" in lower or "xml" in lower:
        # 「jsonで返す」「json形式」「jsonオブジェクト」など
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
            # hard は TB v3 では保持しない（後続で破棄）
            hard.append(text)
        else:
            # soft / unknown は soft 側に寄せる（unknown はユーザー意図を尊重）
            soft.append(text)

    return {"soft": soft, "hard": hard}


# ============================================================
# 内部ヘルパ: 粒度制御（granularity control）
# ============================================================

def _is_low_value_message(text: str) -> bool:
    """
    低価値なメッセージ（TB に長期保存する必要が薄いもの）を判定する。
    - 短い数字だけの発話（「1」「10」など）
    - ごく短い相槌・挨拶
    """
    if not text:
        return True

    s = str(text).strip()
    if not s:
        return True

    # 純粋な数字のみ（短いもの）はノイズ扱い
    if s.isdigit() and len(s) <= 3:
        return True

    # 典型的な相槌・挨拶（最小セット）
    low_words = {"ok", "OK", "Ok", "了解", "うん", "はい", "おけ", "おｋ", "ありがとう", "サンキュー"}
    if s in low_words:
        return True

    # 2〜3文字のひらがなだけ（「あー」「ん？」など）は基本ノイズ
    if len(s) <= 3 and all("ぁ" <= ch <= "ん" for ch in s):
        return True

    return False


def _dedup_preserve_order(items: List[str], max_items: int) -> List[str]:
    """
    重複排除しつつ順序を維持し、最大件数を制限する。
    """
    seen = set()
    result: List[str] = []
    for x in items:
        if not x:
            continue
        if x in seen:
            continue
        seen.add(x)
        result.append(x)
        if len(result) >= max_items:
            break
    return result


def _compress_history_digest(text: str, max_len: int = 800) -> str:
    """
    history_digest が長すぎる場合に、前後を残して中間を省略する。
    """
    if not text:
        return ""
    if len(text) <= max_len:
        return text

    head_len = max_len // 2
    tail_len = max_len - head_len - 10  # " ... " 等の余白
    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    return head + "\n...[snip]...\n" + tail


def _compress_recent_messages(recent: List[str], max_items: int = 5) -> List[str]:
    """
    recent_messages から低価値ノイズを除去しつつ、最大件数を制限する。
    """
    if not isinstance(recent, list):
        return []

    filtered: List[str] = []
    for raw in recent:
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s:
            continue
        if _is_low_value_message(s):
            continue
        filtered.append(s)

    if not filtered:
        return []

    # 直近を優先（末尾が新しいと仮定）
    filtered = filtered[-max_items:]
    return filtered


def _apply_granularity(tb_v3: Dict[str, Any]) -> Dict[str, Any]:
    """
    TB v3 に対して粒度制御を適用し、v3.1 として安定化させる。
    - decisions / unresolved / next_actions / constraints_soft の重複除去・上限数
    - recent_messages のノイズ除去・上限数
    - history_digest の長さ制限
    - meta.version / updated_at の整備
    """
    if not isinstance(tb_v3, dict):
        return tb_v3

    out = dict(tb_v3)

    # --- meta ---
    meta = dict(out.get("meta") or {})
    meta["version"] = "3.1"
    # updated_at が無ければ、または v3.1 で上書きしたい場合は現在時刻を入れる
    try:
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        # 時刻取得失敗時はそのまま
        pass
    out["meta"] = meta

    # --- list 系フィールドの重複除去・制限 ---
    def norm_list(key: str, max_items: int = 10) -> None:
        vals = out.get(key)
        if not isinstance(vals, list):
            out[key] = []
            return
        # 文字列以外も来る可能性があるので文字列化して扱う
        str_items = [str(v).strip() for v in vals if str(v).strip()]
        out[key] = _dedup_preserve_order(str_items, max_items)

    norm_list("decisions", max_items=12)
    norm_list("unresolved", max_items=12)
    norm_list("next_actions", max_items=12)
    norm_list("constraints_soft", max_items=12)

    # --- history_digest ---
    history_digest = out.get("history_digest") or ""
    out["history_digest"] = _compress_history_digest(str(history_digest), max_len=800)

    # --- recent_messages ---
    recent = out.get("recent_messages")
    if isinstance(recent, list):
        # recent が dict などの場合もあるかもしれないので、文字列に寄せる
        str_msgs: List[str] = []
        for m in recent:
            if isinstance(m, str):
                str_msgs.append(m)
            else:
                # "role: content" 形式などに落とすこともできるが、ここでは安全側で str()
                s = str(m).strip()
                if s:
                    str_msgs.append(s)
        out["recent_messages"] = _compress_recent_messages(str_msgs, max_items=5)
    else:
        out["recent_messages"] = []

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
        return {
            "meta": {
                "version": "3.1",
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
            "version": "3.1",
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
        v3.1 形式（constraints_soft のみ＋粒度制御済み）に正規化して返す。
      - None の場合は None を返す。
    """
    if summary is None:
        return None

    if not isinstance(summary, dict):
        tb_v3 = _upgrade_to_v3({})
        return _apply_granularity(tb_v3)

    meta = summary.get("meta") or {}
    version = str(meta.get("version", "")).strip()

    if version.startswith("3"):
        # すでに v3 系想定。最低限のフィールドを満たさせたうえで粒度制御。
        tb_v3 = dict(summary)
        if "constraints_soft" not in tb_v3 or not isinstance(tb_v3["constraints_soft"], list):
            tb_v3["constraints_soft"] = []
        return _apply_granularity(tb_v3)

    # v1/v2 → v3.1 へアップグレード
    tb_v3 = _upgrade_to_v3(summary)
    return _apply_granularity(tb_v3)


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
    lines.append(f"version: {meta.get('version', '3.1')}")
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

    # Recent Messages（粒度制御済みのものだけ）
    if recent_messages:
        lines.append("\n[RECENT_MESSAGES]")
        for msg in recent_messages[-5:]:
            lines.append(f"- {msg}")

    return "\n".join(lines)