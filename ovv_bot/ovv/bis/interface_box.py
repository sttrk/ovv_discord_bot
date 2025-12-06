# ovv/bis/interface_box.py
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
        "user_text": str,
        "runtime_memory": List[dict],
        "thread_brain": Optional[dict],
        "state": Optional[dict],          # ← ここ重要: state_hint を "state" キーで束ねる
        "tb_prompt": str,
        "tb_scoring": str,
      }

MUST:
  - Thread Brain / Runtime Memory / State を「Ovv Core がそのまま食べられる構造」に整形する。
  - Thread Brain のフィルタリングは constraint_filter に委譲し、自分では新しい制約を付け足さない。
  - 新しい意味内容を勝手に生成しない（並べ替え・ラベル付けのみ）。

MUST NOT:
  - DB I/O を直接行わない。
  - Discord / Notion など外部 I/O を行わない。
"""

from typing import List, Optional, Dict, Any

# Thread Brain → テキスト化 / スコアリング
from ovv.brain.threadbrain_adapter import build_tb_prompt
from ovv.brain.tb_scoring import build_scoring_prompt

# Thread Brain Constraint Filter（ノイズ制約の除去）
from ovv.bis.constraint_filter import filter_constraints_from_thread_brain


def build_input_packet(
    user_text: str,
    runtime_memory: List[dict],
    thread_brain: Optional[dict],
    state_hint: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Interface_Box のメイン API。

    - user_text: ユーザの生テキスト
    - runtime_memory: 直近会話履歴（PG runtime_memory 由来）
    - thread_brain: Thread Brain summary (JSON) または None
    - state_hint: state_manager.decide_state が返す軽量 state dict

    戻り値:
      - Ovv Call Layer (ovv_call.call_ovv) にそのまま渡せる InputPacket dict。
    """

    # 1) Thread Brain の制約フィルタリング
    safe_tb = filter_constraints_from_thread_brain(thread_brain) if thread_brain else None

    # 2) TB プロンプト / TB スコアリング
    tb_prompt = build_tb_prompt(safe_tb) if safe_tb else ""
    tb_scoring = build_scoring_prompt(safe_tb) if safe_tb else "[TB-Scoring]\nNo summary available. Prioritize clarity."

    # 3) InputPacket を構築（将来拡張に備え version を固定フィールドとして持つ）
    packet: Dict[str, Any] = {
        "version": "1.0",
        "user_text": user_text,
        "runtime_memory": runtime_memory,
        "thread_brain": safe_tb,
        # NOTE:
        #   - ovv_call 側は "state" キーを参照する。
        #   - state_hint をそのまま "state" に格納して渡す。
        "state": state_hint or {},
        "tb_prompt": tb_prompt,
        "tb_scoring": tb_scoring,
    }

    return packet