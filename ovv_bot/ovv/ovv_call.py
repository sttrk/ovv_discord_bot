# ovv/ovv_call.py
# Ovv Call Layer - September Stable Edition

from typing import List
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

SYSTEM_PROMPT = f"""
あなたは Discord 上の Ovv です。
次の Ovv Soft-Core を保持してください。

{OVV_SOFT_CORE}
""".strip()

# ============================================================
# OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# FINAL 抽出フィルタ
# ============================================================
def _extract_final(text: str) -> str:
    """
    Ovv コアから返ってきたテキストから [FINAL] 以降だけを取り出す。
    マーカーが無い場合はそのまま返す（後方互換性のため）。
    """
    marker = "[FINAL]"
    idx = text.rfind(marker)
    if idx == -1:
        return text

    tail = text[idx + len(marker):].strip()
    return tail if tail else text


# ============================================================
# call_ovv: Ovv Main Logic
# ============================================================
def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    for m in recent_mem[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        raw = res.choices[0].message.content.strip()
        final_text = _extract_final(raw)
        return final_text[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        return "Ovv コア処理中にエラーが発生しました。"
