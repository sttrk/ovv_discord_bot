# ovv/ovv_call.py
# Ovv Call Layer - A4-R3 Stable Edition

from typing import List, Optional
from openai import OpenAI
from config import OPENAI_API_KEY

# ============================================================
# Load Core / External
# ============================================================
from ovv.core_loader import load_core, load_external

OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

# ============================================================
# Soft-Core
# ============================================================
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

SYSTEM_PROMPT_BASE = f"""
あなたは Discord 上の Ovv です。
次の Ovv Soft-Core を厳格に保持してください。

{OVV_SOFT_CORE}
""".strip()

# ============================================================
# OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# A4-R3 context payload builder
# ============================================================
def build_context_payload(thread_brain: Optional[dict]) -> str:
    if not thread_brain:
        return "(no thread_brain)"

    meta = thread_brain.get("meta", {})
    status = thread_brain.get("status", {})
    decisions = thread_brain.get("decisions", [])[:3]
    unresolved = thread_brain.get("unresolved", [])
    constraints = thread_brain.get("constraints", [])
    next_actions = thread_brain.get("next_actions", [])
    history_digest = thread_brain.get("history_digest", "")
    high_goal = thread_brain.get("high_level_goal", "")
    recent_messages = thread_brain.get("recent_messages", [])[:3]
    current_position = thread_brain.get("current_position", "")

    return f"""
[thread_brain payload A4-R3]
phase: {status.get('phase')}
last_major_event: {status.get('last_major_event')}
high_level_goal: {high_goal}
constraints: {constraints}
unresolved: {unresolved}
decisions(top3): {decisions}
next_actions: {next_actions}
history_digest: {history_digest}
recent_messages(top3): {recent_messages}
current_position: {current_position}
""".strip()


# ============================================================
# call_ovv: Ovv Main Logic (A4-R3)
# ============================================================
def call_ovv(
    context_key: int,
    text: str,
    recent_mem: List[dict],
    thread_brain: Optional[dict]
) -> str:

    context_payload = build_context_payload(thread_brain)

    system_prompt = (
        SYSTEM_PROMPT_BASE
        + "\n\n"
        + "以下は現在のコンテキスト情報（A4-R3形式）です：\n"
        + context_payload
    )

    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    for m in recent_mem[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=msgs,
            temperature=0.5,
        )
        return res.choices[0].message.content.strip()[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        return "Ovv コア処理中にエラーが発生しました。"
