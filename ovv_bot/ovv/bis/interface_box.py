# ovv/interface_box.py
"""
[MODULE CONTRACT]
NAME: interface_box
ROLE: Interface_Box

INPUT:
  - user_text: str
  - runtime_memory: List[dict]
  - thread_brain: Optional[dict]
  - state_hint: Optional[dict]

OUTPUT:
  - InputPacket: dict
      {
        "version": "1.0",
        "user_text": <str>,
        "runtime_memory": <List[dict]>,
        "thread_brain": <Optional[dict]>,        # constraint_filter 済み
        "state_hint": <Optional[dict]>,
        "tb_prompt": <str>,                      # ThreadBrain → 推論向け要約
        "tb_scoring": <str>,                     # ThreadBrain → 優先ルール
      }

MUST:
  - 生の user_text / runtime_memory / thread_brain / state_hint を
    「Ovv が直接食べられる InputPacket」に **構造化するだけ** とする。
  - thread_brain については constraint_filter に通し、
    AI 向け制約・machine 指令を取り除いた上で Ovv に渡す。
  - ThreadBrain Adapter（build_tb_prompt）や TB-Scoring（build_scoring_prompt）を呼び出し、
    その結果を tb_prompt / tb_scoring として InputPacket に格納する。

MUST NOT:
  - Ovv の代わりに推論しない（結論や解釈・助言を生成しない）。
  - user_text や runtime_memory の内容を書き換えない（削除や要約は禁止）。
  - Discord 向けの最終メッセージを生成しない（Stabilizer の責務）。

BOUNDARY:
  - Interface_Box は BIS の「I」層であり、B（Boundary_Gate）と S（Stabilizer）の中間にだけ位置する。
  - Storage（PG / Notion）や Discord API を直接触らない。
"""

from typing import List, Optional, Dict, Any

from ovv.threadbrain_adapter import build_tb_prompt
from ovv.tb_scoring import build_scoring_prompt
from ovv.constraint_filter import filter_constraints_from_thread_brain


def build_input_packet(
    user_text: str,
    runtime_memory: List[dict],
    thread_brain: Optional[Dict[str, Any]],
    state_hint: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Interface_Box のメインエントリ。
    Ovv に渡すための InputPacket を構築する。
    """

    # 1) Thread Brain を constraint_filter に通して「AIにそのまま食べさせても良い形」にする
    safe_tb = filter_constraints_from_thread_brain(thread_brain) if thread_brain else None

    # 2) Thread Brain Adapter / TB-Scoring で、推論向けのテキストを生成
    tb_prompt = build_tb_prompt(safe_tb) if safe_tb else ""
    tb_scoring = build_scoring_prompt(safe_tb) if safe_tb else "[TB-Scoring]\nNo summary available. Prioritize clarity."

    # 3) InputPacket を構築（将来拡張に備え version を固定フィールドとして持つ）
    packet: Dict[str, Any] = {
        "version": "1.0",
        "user_text": user_text,
        "runtime_memory": runtime_memory,
        "thread_brain": safe_tb,
        "state_hint": state_hint,
        "tb_prompt": tb_prompt,
        "tb_scoring": tb_scoring,
    }

    return packet