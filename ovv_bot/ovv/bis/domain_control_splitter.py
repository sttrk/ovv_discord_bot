# ============================================================
# [MODULE CONTRACT]
# NAME: domain_control_splitter
# ROLE: IFACE (Pre-Processing: Memory Classification)
#
# INPUT:
#   - runtime_memory: List[dict]
#
# OUTPUT:
#   - domain_log: List[dict]
#   - control_log: List[dict]
#
# MUST:
#   - classify memory into domain/control based on strict rules
#   - remove ALL LLM-format instructions from domain_log
#   - preserve chronological order
#   - avoid altering original memory entries
#
# MUST_NOT:
#   - perform IO
#   - call Core / Stabilizer / PG / Discord
#   - perform semantic inference beyond allowed rules
#
# DEPENDENCY:
#   - None (pure local logic)
# ============================================================

from typing import List, Tuple, Dict, Any


# ============================================================
# [IFACE] Splitter Main Entry
# ============================================================
def split_memory(runtime_memory: List[Dict[str, Any]]) -> Tuple[List[dict], List[dict]]:
    """
    runtime_memory を domain_log / control_log に分類する。

    domain_log:
        - スレッドの目的・状態・決定・未解決・作業内容 など
        - 長期的意味を持つ情報のみを残す

    control_log:
        - JSONで返して
        - ○○として振る舞え
        - 箇条書きで返答せよ
        - PROMPT専用命令
        - その他 LLM 向け操作指示

    ※ この段階ではまだ「単純分類ロジック」だけ。
       精度は後で段階的に強化する。
    """

    domain_log = []
    control_log = []

    for entry in runtime_memory:
        content: str = entry.get("content", "")

        # -----------------------------------------
        # [RULE] PROMPT指示（[PROMPT]タグ）は control へ
        # -----------------------------------------
        if content.strip().startswith("[PROMPT]"):
            control_log.append(entry)
            continue

        # -----------------------------------------
        # [RULE] 明白なフォーマット指示
        # -----------------------------------------
        if _is_llm_format_instruction(content):
            control_log.append(entry)
            continue

        # -----------------------------------------
        # [RULE] その他すべて domain に入れる（v0.1）
        #         ※ 次バージョンで強化
        # -----------------------------------------
        domain_log.append(entry)

    return domain_log, control_log


# ============================================================
# [IFACE] Helper — Format Instruction Detector
# ============================================================
def _is_llm_format_instruction(text: str) -> bool:
    """
    LLM向けの操作指示かどうかを判定する簡易ルール。
    v0.1 では最小限。後で拡張する。
    """

    lowered = text.lower()

    triggers = [
        "jsonで返して",
        "json形式で返して",
        "jsonで答えて",
        "markdown禁止",
        "箇条書きで返答",
        "あなたは",
        "〜として振る舞え",
        "role:",
        "format:",
        "返答形式",
        "出力形式",
    ]

    return any(t in lowered for t in triggers)