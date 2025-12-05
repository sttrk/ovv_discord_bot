# ovv/state_manager.py
# Ovv State Manager v1 (Lightweight)
#
# 目的:
#  - idle / task / simple_sequence の軽量モード判定
#  - 「数字カウントゲーム」程度の連続性を Ovv にヒントとして渡す

from typing import List, Dict, Optional


def _parse_int(text: str) -> Optional[int]:
    """メッセージがほぼ数字だけなら int に変換する簡易パーサ。"""
    if text is None:
        return None
    t = text.strip()
    # 末尾の句読点などを軽く除去
    for ch in ["。", ".", "！", "!", "？", "?"]:
        if t.endswith(ch):
            t = t[:-1].strip()
    if not t:
        return None
    if not (t.lstrip("-").isdigit()):
        return None
    try:
        return int(t)
    except Exception:
        return None


def _find_last_numbers(recent_mem: List[dict]) -> Dict[str, Optional[int]]:
    """
    recent_mem から、直近の user / assistant 数字っぽいメッセージを拾う。
    """
    last_user_num = None
    last_assistant_num = None

    # 後ろから走査
    for m in reversed(recent_mem):
        role = m.get("role")
        content = m.get("content", "")
        n = _parse_int(content)
        if n is None:
            continue

        if role == "user" and last_user_num is None:
            last_user_num = n
        elif role == "assistant" and last_assistant_num is None:
            last_assistant_num = n

        if last_user_num is not None and last_assistant_num is not None:
            break

    return {
        "last_user_num": last_user_num,
        "last_assistant_num": last_assistant_num,
    }


def decide_state(
    context_key: int,
    user_text: str,
    recent_mem: List[dict],
    task_mode: bool,
) -> Optional[dict]:
    """
    軽量ステート判定を行い、state_hint を返す。
    返り値:
      - None        → 通常の idle 扱い（特別なヒントなし）
      - dict(...)   → ovv_call 側に渡す state_hint
    """

    # 1) task_mode は最優先（Notion / thread_brain による管理）
    if task_mode:
        return {
            "mode": "task",
            "context_key": context_key,
        }

    # 2) simple_sequence（数字カウント）判定
    #    「直前まで数字のやりとりをしている」 + 「今回も数字 or '次'」なら simple_sequence とみなす
    info = _find_last_numbers(recent_mem)
    last_user_num = info["last_user_num"]
    last_assistant_num = info["last_assistant_num"]

    u_num = _parse_int(user_text)
    u_str = (user_text or "").strip()

    # 「次 / next / 続き」パターン
    next_words = {"次", "つぎ", "next", "NEXT", "Next", "続き", "continue"}
    if u_str in next_words and last_assistant_num is not None:
        return {
            "mode": "simple_sequence",
            "context_key": context_key,
            "base_number": last_assistant_num,
            "reason": "keyword_next",
        }

    # 「ひたすら数字を一つずつ増やしている」パターン
    if u_num is not None:
        # 直近 user → assistant → user の流れをざっくり見て、
        # user が連番を打っているっぽければ simple_sequence とする
        if last_user_num is not None and u_num == last_user_num + 1:
            return {
                "mode": "simple_sequence",
                "context_key": context_key,
                "base_number": u_num,
                "reason": "user_increment",
            }
        if last_assistant_num is not None and u_num == last_assistant_num + 1:
            return {
                "mode": "simple_sequence",
                "context_key": context_key,
                "base_number": u_num,
                "reason": "assistant_increment",
            }

    # 3) それ以外は idle
    return None
