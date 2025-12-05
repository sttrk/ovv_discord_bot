# ovv/ovv_call.py
# Ovv Call Layer - September Stable Edition (Context-Aware)

from typing import List, Dict, Optional

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External
from ovv.core_loader import load_core, load_external

# コンテキスト整形（Bモード）
from ovv.ovv_context_manager import build_ovv_context_block

# PG: thread_brain / runtime_memory / audit 用
import database.pg as db_pg


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
# call_ovv: Ovv Main Logic (Bモード: thread_brain + mem 利用)
# ============================================================
def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    """
    - PG に保存された thread_brain を取得
    - runtime_memory と合わせて自然文コンテキストを組み立てる
    - それを Ovv への前置きコンテキストとして渡す
    """

    # 1) thread_brain を PG から取得
    tb_summary: Optional[Dict] = db_pg.load_thread_brain(context_key)

    # 2) 自然文のコンテキストブロックを生成
    ctx_block: str = build_ovv_context_block(
        context_key=context_key,
        thread_brain_summary=tb_summary,
        recent_mem=recent_mem,
    )

    # 3) LLM へのメッセージ構成
    msgs: List[Dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
        # ★ Bモード追加: thread_brain + recent_mem の自然文ダイジェスト
        {"role": "assistant", "content": ctx_block},
    ]

    # 直近の「生ログ」を少しだけそのまま付ける（最大 10 件）
    for m in recent_mem[-10:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    # 最後に今回のユーザー発話
    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()

        # runtime_memory にアシスタント応答を追記
        db_pg.append_runtime_memory(str(context_key), "assistant", ans)

        # 監査ログ
        db_pg.log_audit("assistant_reply", {
            "context_key": context_key,
            "length": len(ans),
        })

        return ans[:1900]

    except Exception as e:
        db_pg.log_audit("openai_error", {
            "context_key": context_key,
            "user_text": text[:500],
            "error": repr(e),
        })
        return "Ovv コア処理中にエラーが発生しました。"
