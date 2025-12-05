# ovv/interface_box.py
# Interface_Box - BIS Architecture (B → I → Ovv → S)
#
# 役割:
#  - runtime_memory / thread_brain / state_hint を統合し、
#    Ovv 推論に最適化された input packet を構成する。
#  - Ovv Core 自身に余計な前処理をさせないための「前処理箱」。

from typing import List, Dict, Optional, Any

import database.pg as db_pg
from ovv.threadbrain_adapter import build_tb_prompt
from ovv.tb_scoring import build_scoring_prompt
from ovv.state_manager import decide_state


def build_ovv_input(
    context_key: int,
    user_text: str,
    recent_mem: List[dict],
    task_mode: bool,
) -> Dict[str, Any]:
    """
    Interface_Box:
    - Thread Brain / Scoring / State Hint / trimmed memory をまとめた input packet を生成する。
    - Ovv Core はこの packet を前提に messages を構成する。

    戻り値の dict 構造:
    {
      "context_key": int,
      "user_text": str,
      "trimmed_mem": List[dict],
      "thread_brain": Optional[dict],
      "tb_prompt": str,
      "tb_scoring": str,
      "state_hint": Optional[dict],
      "task_mode": bool,
    }
    """

    # 1) runtime memory のトリミング（最大 20 件）
    if recent_mem is None:
        recent_mem = []
    trimmed_mem = recent_mem[-20:]

    # 2) thread_brain の読み出し
    try:
        tb = db_pg.load_thread_brain(context_key)
    except Exception as e:
        print("[Interface_Box] load_thread_brain error:", repr(e))
        tb = None

    # 3) TB -> prompt / scoring 変換
    tb_prompt = build_tb_prompt(tb) if tb else ""
    tb_scoring = build_scoring_prompt(tb)

    # 4) 軽量ステート判定
    try:
        state_hint = decide_state(
            context_key=context_key,
            user_text=user_text,
            recent_mem=recent_mem,
            task_mode=task_mode,
        )
    except Exception as e:
        print("[Interface_Box] decide_state error:", repr(e))
        state_hint = None

    return {
        "context_key": context_key,
        "user_text": user_text,
        "trimmed_mem": trimmed_mem,
        "thread_brain": tb,
        "tb_prompt": tb_prompt,
        "tb_scoring": tb_scoring,
        "state_hint": state_hint,
        "task_mode": task_mode,
    }