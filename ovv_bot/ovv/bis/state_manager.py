# ovv/bis/state_manager.py
"""
[MODULE CONTRACT]
NAME: state_manager
ROLE: StateHintGenerator (BIS-S Layer)

INPUT:
  - context_key: int
  - user_text: str
  - recent_mem: list[dict]   # runtime_memory (PG) 由来
  - task_mode: bool          # task_ チャンネルかどうか

OUTPUT:
  - state_hint: dict
      {
        "context_key": int,
        "mode": "task" | "general" | "idle",
        "turn_index": int,
        "flow": "start" | "continue" | "reset",
        "length": "empty" | "short" | "medium" | "long",
        "has_question": bool,
      }

MUST:
  - generate_lightweight_state_hint           # 軽量なメタ情報のみを生成する
  - avoid_semantic_inference                  # 意味推論に踏み込まない
  - be_deterministic_given_inputs             # 同じ入力には同じ出力
  - remain_stable_under_noise                 # ノイズの多い会話でも破綻しない

MUST_NOT:
  - call_LLM                                  # LLM 呼び出しは禁止
  - perform_IO                                # PG / Notion など外部 I/O はしない
  - interpret_long_term_intent                # 長期意図の解釈はしない（TB v3 に委譲）
  - override_OvvCore_decisions                # Core の判断を先回りして決めない

BOUNDARY:
  - このモジュールは BIS の「S」レイヤの一部として、state_hint という軽量メタ情報のみを提供する。
  - ThreadBrain / Interface_Box / Ovv Core と疎結合であり、意味解釈ではなく構造的ヒントに限定する。
"""

from typing import List, Dict, Optional


# ============================================================
# 内部ユーティリティ
# ============================================================

def _normalize_text(text: Optional[str]) -> str:
    """None / 空白を安全に処理した上で str に変換する。"""
    if not text:
        return ""
    return str(text).strip()


def _count_user_turns(recent_mem: List[dict]) -> int:
    """runtime_memory 内の 'user' ロールの件数を数える。"""
    count = 0
    for m in recent_mem:
        if m.get("role") == "user":
            count += 1
    return count


def _classify_length(text: str) -> str:
    """
    発話長を簡易分類する。
      - empty : 文字数 0
      - short : 1〜30
      - medium: 31〜120
      - long  : 121 以上
    """
    length = len(text)
    if length == 0:
        return "empty"
    if length <= 30:
        return "short"
    if length <= 120:
        return "medium"
    return "long"


def _has_question_mark(text: str) -> bool:
    """記号レベルの質問っぽさのみ判定（意味までは読まない）。"""
    if not text:
        return False
    return ("?" in text) or ("？" in text)


def _detect_topic_reset(text: str) -> bool:
    """
    「話題リセット」らしき合図を最小限の語彙で検出する。
    ※ 意味推論ではなく、固定フレーズ検出に限定する。
    """
    t = _normalize_text(text)
    if not t:
        return False

    reset_keywords = [
        "リセット",
        "reset",
        "最初から",
        "一旦整理",
        "話題変えて",
        "別の話",
    ]
    return any(k in t for k in reset_keywords)


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
    Discord 版 Ovv 向けの汎用 state_hint を生成する。

    返り値例:
    {
      "context_key": 1234567890,
      "mode": "task",
      "turn_index": 5,
      "flow": "continue",
      "length": "medium",
      "has_question": True,
    }

    特徴:
      - 意味推論には踏み込まず、構造・長さ・記号など「薄いメタ情報」に限定する。
      - None を返さず、常に dict を返す。
      - TB v3 / Ovv Core との責務分離を徹底する。
    """

    text_norm = _normalize_text(user_text)

    # 1) mode 判定
    if task_mode:
        mode = "task"
    else:
        # 会話履歴がほぼ無い場合は idle、それ以外は general
        mode = "idle" if len(recent_mem) == 0 else "general"

    # 2) ユーザー発話回数（turn_index）
    # recent_mem には直前までの履歴＋今回発話が含まれている前提
    user_turns = _count_user_turns(recent_mem)
    if user_turns <= 0:
        # 念のため安全側フォールバック
        user_turns = 1
    turn_index = user_turns

    # 3) flow 判定
    if turn_index <= 1:
        flow = "start"
    elif _detect_topic_reset(text_norm):
        flow = "reset"
    else:
        flow = "continue"

    # 4) length 判定
    length_tag = _classify_length(text_norm)

    # 5) 質問記号の有無
    has_q = _has_question_mark(text_norm)

    state_hint: Dict[str, object] = {
        "context_key": context_key,
        "mode": mode,
        "turn_index": turn_index,
        "flow": flow,
        "length": length_tag,
        "has_question": has_q,
    }

    return state_hint