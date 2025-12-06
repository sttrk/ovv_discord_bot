# ovv/state_manager.py
# Ovv State Manager v2 (Generic State Hint for BIS)
#
# 目的:
#   - 特定の「数字カウントゲーム」専用ではなく、
#     一般会話・タスク会話の両方で使える「汎用ステート」を提供する。
#   - BIS の Interface_Box に、「いまの会話状態」を簡易メタ情報として渡す。
#
# 出力イメージ (state_hint):
# {
#   "context_key": int,
#   "mode": "general" | "task" | "idle",
#   "intent_state": "sustain" | "drift" | "shift",
#   "tension": 0 | 1 | 2 | 3,
#   "progress": 0 | 1 | 2,
# }
#
# 既存コードとの互換性:
#   - シグネチャは従来通り:
#       decide_state(context_key, user_text, recent_mem, task_mode)
#   - 単純な dict を返すだけであり、以前の simple_sequence モードは廃止。
#   - state_hint を使わない実装でも副作用なし。


from typing import List, Dict, Optional
from datetime import datetime, timezone


# ============================================================
# 内部ユーティリティ
# ============================================================

def _get_last_user_message(recent_mem: List[dict]) -> Optional[str]:
    """直近の user メッセージを後ろから走査して 1 件返す。"""
    for m in reversed(recent_mem):
        if m.get("role") == "user":
            return m.get("content") or ""
    return None


def _normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return str(text).strip()


def _is_question(text: str) -> bool:
    """簡易的な「質問らしさ」判定。"""
    if not text:
        return False
    if "?" in text or "？" in text:
        return True
    # よくある日本語の質問語
    question_keywords = ["教えて", "どう", "なに", "何", "どこ", "どれ", "なぜ", "なんで", "ですか", "でしょうか"]
    return any(k in text for k in question_keywords)


def _estimate_tension(recent_mem: List[dict], user_text: str) -> int:
    """
    軽量な「テンション（苛立ち/違和感の強さ）」推定。
    0: 通常
    1: 違和感・軽い不満
    2: 明確な不満・苛立ち
    3: 強い苛立ち・トラブルモード
    """
    text = _normalize_text(user_text)
    if not text:
        return 0

    # 直近数メッセージを対象に軽量判定
    window = list(reversed(recent_mem[-5:]))

    # ネガティブワードの簡易セット（日本語中心）
    mild_words = ["ん？", "え？", "違う", "おかしい", "変だ"]
    strong_words = ["ふざけ", "ムカつく", "最悪", "使えない", "ダメ", "なんでやねん", "は？"]

    score = 0

    def scan(s: str) -> int:
        s_norm = _normalize_text(s)
        if not s_norm:
            return 0
        local = 0
        if any(w in s_norm for w in mild_words):
            local += 1
        if any(w in s_norm for w in strong_words):
            local += 2
        return local

    # 現在ユーザー入力
    score += scan(text)

    # 過去数件（user のみ）も軽く見る
    for m in window:
        if m.get("role") != "user":
            continue
        score += scan(m.get("content", ""))

    # 正規化（最大 3）
    if score <= 0:
        return 0
    if score == 1:
        return 1
    if 2 <= score <= 3:
        return 2
    return 3


def _estimate_intent_state(user_text: str, last_user_text: Optional[str]) -> str:
    """
    intent_state:
      - sustain: 直前までの話題の継続っぽい
      - drift  : 少し話題がズレつつも関連はありそう
      - shift  : 完全に話題が変わった / 新規トピックの可能性が高い
    """
    current = _normalize_text(user_text)
    last = _normalize_text(last_user_text)

    if not last:
        # 過去 user 発話がない → 新規トピック
        return "shift"

    # 質問かどうか
    current_q = _is_question(current)
    last_q = _is_question(last)

    # 長さと overlap を簡易評価
    # （本格的な類似度は使わず、トークンの粗い共通部分を見る）
    current_tokens = set(current.replace("、", " ").replace("。", " ").split())
    last_tokens = set(last.replace("、", " ").replace("。", " ").split())
    overlap = len(current_tokens & last_tokens)

    # かなり粗いヒューリスティックだが、軽量で壊れない範囲に留める
    if overlap >= 3:
        # 単語の被りが多い → ほぼ同一トピック
        return "sustain"

    if current_q != last_q:
        # 文の性質が質問⇔回答系で切り替わっている場合は drift 気味
        return "drift"

    if overlap == 0 and len(current_tokens) >= 3:
        # ほぼ共通語無しの新規発話
        return "shift"

    # デフォルトは drift に寄せる
    return "drift"


def _estimate_progress(user_text: str, recent_mem: List[dict]) -> int:
    """
    progress:
      0: 停滞・戸惑い寄り（「え？」「違う」「なんで」系）
      1: 通常進行（挨拶・軽い返答など）
      2: 前進度高め（新しい問い / 明確なリクエスト）
    """
    text = _normalize_text(user_text)
    if not text:
        return 0

    # ごく簡単な「戸惑い/停止」パターン
    stop_words = ["え？", "は？", "なんで", "違う", "わからない", "どういうこと"]
    if any(w in text for w in stop_words):
        return 0

    # 新しい質問・依頼っぽいものは 2
    if _is_question(text):
        return 2
    request_keywords = ["してほしい", "やって", "作って", "教えて", "設計", "実装"]
    if any(k in text for k in request_keywords):
        return 2

    # それ以外は 1（会話は進んでいる）
    return 1


# ============================================================
# メイン: decide_state
# ============================================================

def decide_state(
    context_key: int,
    user_text: str,
    recent_mem: List[dict],
    task_mode: bool,
) -> Dict[str, object]:
    """
    汎用ステート判定を行い、state_hint を返す。

    返り値例:
    {
      "context_key": 1234567890,
      "mode": "task",
      "intent_state": "sustain",
      "tension": 1,
      "progress": 2,
    }

    ※ 以前の実装との違い:
      - simple_sequence や数字カウント専用モードは廃止。
      - None を返さず、常に dict を返す（互換性は保たれる）。
    """

    # 1) mode 判定
    if task_mode:
        mode = "task"
    else:
        # recent_mem がほぼ無い場合は idle に寄せる
        mode = "idle" if len(recent_mem) == 0 else "general"

    # 2) 直近ユーザー発話
    last_user_text = _get_last_user_message(recent_mem[:-1])  # 今回発話を除く

    # 3) intent_state 推定
    intent_state = _estimate_intent_state(user_text, last_user_text)

    # 4) tension 推定
    tension = _estimate_tension(recent_mem, user_text)

    # 5) progress 推定
    progress = _estimate_progress(user_text, recent_mem)

    state_hint: Dict[str, object] = {
        "context_key": context_key,
        "mode": mode,
        "intent_state": intent_state,
        "tension": tension,
        "progress": progress,
    }

    return state_hint