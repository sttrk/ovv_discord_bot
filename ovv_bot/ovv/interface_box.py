# ovv/interface_box.py
# BIS Layer: Interface_Box (InputPacket Builder)
#
# 目的:
#   - Boundary_Gate から渡された raw 情報を、
#     Ovv Core が処理できる「InputPacket」に整形する。
#   - ThreadBrain（長期文脈）と TB-Scoring（優先ルール）を統合し、
#     Ovv に渡す前処理の標準化を行う。
#
# 出力:
#   dict {
#       "user_text": str,
#       "runtime_memory": [...],
#       "thread_brain_text": str or "",
#       "scoring_hint": str or "",
#       "state_hint": dict or None
#   }


from typing import Optional, List, Dict, Any

from ovv.threadbrain_adapter import build_tb_prompt
from ovv.tb_scoring import build_scoring_prompt


def _safe(obj: Any) -> Any:
    """None → "", それ以外はそのまま返す簡易サニタイザ。"""
    return "" if obj is None else obj


def build_input_packet(
    user_text: str,
    runtime_memory: List[dict],
    thread_brain: Optional[Dict],
    state_hint: Optional[Dict],
) -> Dict[str, Any]:
    """
    Boundary_Gate から渡された情報をもとに InputPacket を構築する。
    - user_text: ユーザーの最新発話（文字列）
    - runtime_memory: 最近の会話メモリ（PG runtime_memory）
    - thread_brain: ThreadBrain summary（辞書 or None）
    - state_hint: state_manager が返す軽量ステート（辞書 or None）
    """

    # ============================================================
    # 1. ThreadBrain の長期文脈をテキスト化
    # ============================================================
    tb_text = ""
    if thread_brain:
        try:
            tb_text = build_tb_prompt(thread_brain)
        except Exception:
            tb_text = ""  # ThreadBrain が壊れていてもクラッシュしない

    # ============================================================
    # 2. TB-Scoring の優先ルールセット
    # ============================================================
    scoring_text = ""
    if thread_brain:
        try:
            scoring_text = build_scoring_prompt(thread_brain)
        except Exception:
            scoring_text = ""

    # ============================================================
    # 3. InputPacket 組み立て
    # ============================================================
    packet = {
        "user_text": _safe(user_text),
        "runtime_memory": runtime_memory,
        "thread_brain_text": tb_text,
        "scoring_hint": scoring_text,
        "state_hint": state_hint,
    }

    return packet