# ovv/brain/threadbrain_generator.py
# ThreadBrain Generator – v1.0 (Stable Minimal Edition)
#
# [MODULE CONTRACT]
# NAME: threadbrain_generator
# ROLE: CORE-AUX (Ovv-Core 用補助モジュール)
#
# INPUT:
#   - context_key: int
#   - runtime_memory: list[dict]
#
# OUTPUT:
#   - tb_summary: dict (ThreadBrain v3 仕様に準拠)
#
# MUST:
#   - Proposal → Audit → Final の 3段階要約を行う
#   - runtime_memory を破壊しない
#   - LLM への過剰な命令文を混入させない
#
# MUST_NOT:
#   - Discord API へ触れない
#   - Postgres へ直接 I/O を行わない
#   - TB v3 仕様に反する構造を生成しない
#
# DEPENDENCY:
#   - openai  (Ovv-Core と同じ API を使用)
#   - runtime_memory のみ
#

from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import os

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ============================================================
# build_tb_prompt_base – LLM に説明する TB の骨格
# ============================================================
def _build_tb_prompt_base(runtime_memory: List[dict]) -> str:
    """
    ThreadBrain 要約生成のための最小プロンプト。
    Domain-only TB を生成するため、形式指示やロール指示は含めない。
    """

    # Domain のみ抽出（kind 制御が存在しない場合でも安全に動作）
    domain_logs = [
        f"{m['role']}: {m['content']}"
        for m in runtime_memory
        if m.get("kind") in ("domain", None)
    ]

    joined = "\n".join(domain_logs[-40:])  # TB生成対象は最新40件程度で十分

    base_prompt = f"""
You are the ThreadBrain summarizer.

Summarize the conversation into the following stable structure (TB v3):

- status
- decisions
- unresolved
- next_actions
- history_digest
- high_level_goal

The summary must include ONLY:
- domain-relevant decisions
- goals
- open issues
- next steps

It must NOT include:
- JSON formatting commands
- markdown restrictions
- meta instructions to the model
- temporary game rules or playful constraints

Conversation log:
{joined}

Generate the TB summary now.
""".strip()

    return base_prompt


# ============================================================
# generate_tb_summary – TB v3 要約生成
# ============================================================
def generate_tb_summary(context_key: int, runtime_memory: List[dict]) -> Optional[dict]:
    """
    Generate ThreadBrain summary using OpenAI.
    Returns TB v3 dict.
    """

    if not runtime_memory:
        return None

    prompt = _build_tb_prompt_base(runtime_memory)

    # ===== LLM Call =====
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are the ThreadBrain summarizer."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=600,
        )
    except Exception as e:
        print(f"[TB] Generation error: {e}")
        return None

    text = res.choices[0].message["content"]

    # ===== JSON 抽出 =====
    # LLM から返る自然文 → JSON を抽出する
    import re
    import json

    match = re.search(r"\{[\s\S]+\}", text)
    if not match:
        print("[TB] No JSON found.")
        return None

    try:
        tb = json.loads(match.group())
    except Exception as e:
        print(f"[TB] JSON parse error: {e}")
        return None

    # meta を必ず付与
    tb["meta"] = {
        "version": "3.0",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "context_key": context_key,
        "total_tokens_estimate": res.usage.total_tokens if hasattr(res, "usage") else None,
    }

    return tb
