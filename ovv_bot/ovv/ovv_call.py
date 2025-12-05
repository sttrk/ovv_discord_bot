# ovv/ovv_call.py
# Ovv Call Layer - September Stable Edition (FINAL-only)

from typing import List
from openai import OpenAI

from config import OPENAI_API_KEY
from ovv.core_loader import load_core, load_external

# ============================================================
# Load Core / External
# ============================================================

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
# Helper: FINAL 部だけを抽出
# ============================================================

def _extract_final(text: str) -> str:
    """
    Ovv Core が [FINAL] や【FINAL】を含む場合、
    FINAL セクションのみを返す。
    なければ text 全体を返す（後方互換）。
    """
    if not text:
        return text

    markers = ["[FINAL]", "【FINAL】", "[ Final ]", "[FINAL OUTPUT]"]
    idx = -1
    for m in markers:
        pos = text.find(m)
        if pos != -1:
            idx = pos + len(m)
            break

    if idx == -1:
        return text.strip()

    return text[idx:].strip()


# ============================================================
# call_ovv: Ovv Main Logic (DB には一切依存しない)
# ============================================================

def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    """
    - PG や Notion には一切触れない「純粋な推論レイヤ」
    - メモリ・監査は呼び出し側（bot.py）が責務を持つ
    """
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
        final = _extract_final(raw)
        return final[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        return "Ovv コア処理中にエラーが発生しました。"
