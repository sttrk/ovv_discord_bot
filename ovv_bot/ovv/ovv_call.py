# ovv/ovv_call.py
# A-2 + A-3 完全統合版（Thread Brain + Scoring Layer）

from typing import List
from openai import OpenAI
from config import OPENAI_API_KEY

from ovv.core_loader import load_core, load_external
from ovv.threadbrain_adapter import build_tb_prompt
from ovv.tb_scoring import build_scoring_prompt
from database.pg import load_thread_brain

OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary
2. MUST use Clarify only when needed
3. MUST avoid hallucination
4. MUST respect boundaries
5. SHOULD decompose → reconstruct
6. MUST NOT phase-mix
7. MAY trigger CDC sparingly
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上の Ovv です。
次の Ovv Soft-Core を保持してください。

{OVV_SOFT_CORE}
""".strip()

openai_client = OpenAI(api_key=OPENAI_API_KEY)


def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:

    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    # ======================================================
    # A-2: Thread Brain Injection
    # ======================================================
    tb_summary = load_thread_brain(context_key)
    tb_prompt = build_tb_prompt(tb_summary)
    if tb_prompt:
        msgs.append({"role": "assistant", "content": f"[Thread Brain]\n{tb_prompt}"})

    # ======================================================
    # A-3: Scoring Layer Injection（最優先ルール）
    # ======================================================
    scoring = build_scoring_prompt(tb_summary)
    msgs.append({"role": "assistant", "content": scoring})

    # Memory
    for m in recent_mem[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        return res.choices[0].message.content.strip()[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        return "Ovv コア処理中にエラーが発生しました。"
