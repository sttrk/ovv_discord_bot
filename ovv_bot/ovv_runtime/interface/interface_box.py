# ovv/interface_box.py
# Interface_Box v1.0 (A5-Minimal)
#
# 役割:
#   - Boundary_Gate(bot.py) から渡された情報を、Ovv 推論に適した InputPacket に整形する。
#   - chatGPT/Ovv にとって扱いやすい「一塊の入力」を作る。
#   - ここでは DB / Notion には触れない（あくまでメモリとユーザ入力の整形）。
#
# 想定フロー（BIS: Boundary_Gate → Interface_Box → Ovv → Stabilizer）:
#   1. bot.py(on_message) で:
#       - context_key を算出
#       - runtime_memory に user 発話を append & load
#       - Task チャンネルなら thread_brain を更新
#   2. その上で Interface_Box.build_input_packet(...) を呼び出し、
#      Ovv に渡すための InputPacket を組み立てる。
#   3. Ovv 呼び出し時には、InputPacket 内の user_text / recent_mem / state_hint を利用する。
#
# 今回 v1.0 では、「state_hint（簡易ステート）」だけを追加し、
# 将来の A5 拡張で ThreadBrain Adapter や TB-Scoring と統合できる余地を残す。


from typing import List, Dict, Optional

from .state_manager import decide_state


class InputPacket(dict):
    """
    InputPacket v1.0 – Ovv に渡すための標準入力フォーマット

    必須キー:
        - context_key: int
            Discord 上での「文脈キー」(guild, channel, thread をまとめた ID)

        - user_text: str
            今回のユーザー生テキスト（前処理前）。Ovv に渡す主入力。

        - recent_mem: List[dict]
            runtime_memory から取得した直近会話ログ。
            形式は PG runtime_memory と同じ:
              { "role": "user" | "assistant" | "system" | "debug", "content": str, "ts": iso8601 }

        - task_mode: bool
            Task チャンネル/スレッドかどうか（is_task_channel の結果）。

    追加キー（v1.0）:
        - state_hint: dict (任意)
            state_manager.decide_state(...) が返す軽量ステート情報。
            例:
              {
                "mode": "simple_sequence" | "task" | "idle",
                "context_key": 1234567890,
                "base_number": 42,
                "reason": "user_increment",
              }

    将来拡張（v2 以降の想定 – まだ実装しない）:
        - thread_brain: dict
        - tb_prompt: str
        - tb_scoring: str

    """
    pass


def build_input_packet(
    context_key: int,
    user_text: str,
    recent_mem: List[dict],
    task_mode: bool,
) -> InputPacket:
    """
    Interface_Box のメインエントリ。
    Boundary_Gate(bot.py) から渡された情報を InputPacket に整形する。

    - ここでは DB や Notion にはアクセスしない。
    - 役割は「入力の正規化」と「state_hint の付与」に限定する。
    """

    # 軽量ステート判定（数字カウントなど）
    state_hint: Optional[Dict] = decide_state(
        context_key=context_key,
        user_text=user_text,
        recent_mem=recent_mem,
        task_mode=task_mode,
    )

    packet: InputPacket = InputPacket(
        context_key=context_key,
        user_text=user_text,
        recent_mem=recent_mem,
        task_mode=task_mode,
    )

    if state_hint is not None:
        packet["state_hint"] = state_hint

    return packet