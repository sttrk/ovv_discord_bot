# ovv/interface/intent/sketch.py
# ============================================================
# MODULE CONTRACT: Interface / Intent Sketch v0.1 (Minimal)
#
# ROLE:
#   - ユーザーの自由発言（非コマンド）を受け取り、
#     「どうやればできるか」を構造化して返す。
#
# OUTPUT POLICY:
#   - 決定しない
#   - 実行しない
#   - 記録しない
#
# STRUCTURE (FIXED):
#   [Proposal] -> [Audit] -> [Next]
# ============================================================

from __future__ import annotations
from typing import Dict, Any


def build_intent_sketch(user_text: str) -> str:
    """
    ユーザーの発言をもとに、思考下書きを返す。
    ※ 現段階では内容生成はテンプレ固定。
    """

    text = (user_text or "").strip()
    if not text:
        return ""

    return (
        "=== Intent Sketch ===\n\n"
        "[Proposal]\n"
        "- 今のやりたい事を、作業単位に分けて考える\n"
        "- まずは最小構成で試し、後から広げる\n\n"
        "[Audit]\n"
        "- 何を決めきれていないかが未整理\n"
        "- 自動化すると判断ミスが起きる可能性がある\n\n"
        "[Next]\n"
        "- 進めたい方向を一つ選ぶ\n"
        "- WBS に入れるなら !wy で明示的に確定する\n"
    )