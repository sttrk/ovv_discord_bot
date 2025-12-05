# ovv/interface_box.py
# Interface_Box (BIS Layer-2)
# 要点：
# ・Boundary_Gate から渡された raw user text / runtime_memory / thread_brain summary を
#   「Ovv が処理可能な InputPacket」に正規化する。
# ・推論中に勝手な補完は禁止。未定義は "" または None として渡す。
# ・構造破壊を防ぎ、Ovv Core が扱いやすい最小構造へ整形する。

from typing import Dict, List, Optional
from ovv.threadbrain_adapter import build_tb_prompt
from ovv.tb_scoring import build_scoring_prompt


def build_input_packet(
    user_text: str,
    runtime_memory: List[Dict],
    thread_brain: Optional[Dict]
) -> Dict:
    """
    Boundary_Gate → Interface_Box 経由で Ovv へ渡す入力パケットを構築する。
    BIS Layer-2 の最重要関数。

    Input:
        user_text: str              Discord ユーザ発話（1 メッセージ）
        runtime_memory: List[dict]  直近 N件のログ（PG runtime_memory）
        thread_brain: dict | None   thread_brain summary（PG thread_brain）
    
    Output (InputPacket):
        {
            "user_text": "...",
            "runtime_memory_text": "...",    # Ovv が読める形式に直列化
            "thread_brain_prompt": "...",    # build_tb_prompt(summary)
            "tb_scoring": "...",             # build_scoring_prompt(summary)
        }
    """
    # ============================================================
    # 1. Runtime memory → テキスト化（最大 20 件推奨）
    # ============================================================
    rm_lines = []
    for m in runtime_memory[-20:]:
        role = m.get("role", "")
        content = m.get("content", "").replace("\n", " ")
        rm_lines.append(f"{role.upper()}: {content}")

    runtime_text = "\n".join(rm_lines) if rm_lines else "(no recent memory)"

    # ============================================================
    # 2. Thread Brain prompt（長文整形）
    # ============================================================
    tb_prompt = build_tb_prompt(thread_brain) if thread_brain else ""

    # ============================================================
    # 3. TB-Scoring（次発話ポリシー）
    # ============================================================
    scoring_prompt = build_scoring_prompt(thread_brain) if thread_brain else ""

    # ============================================================
    # 4. InputPacket の組み立て
    # ============================================================
    packet = {
        "user_text": user_text,
        "runtime_memory_text": runtime_text,
        "thread_brain_prompt": tb_prompt,
        "tb_scoring": scoring_prompt,
    }

    return packet