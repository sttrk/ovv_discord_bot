# ovv/ovv_call.py
import json
from typing import List
from datetime import datetime, timezone

def call_ovv(
    *,
    openai_client,
    system_prompt: str,
    core_text: str,
    external_text: str,
    context_key: int,
    text: str,
    recent_mem: List[dict],
    append_runtime_memory,
    log_audit,
):
    """
    Ovv 呼び出しの純粋関数化バージョン。
    bot.py から依存を全て注入して使う。

    - openai_client : OpenAI client instance
    - system_prompt : SYSTEM_PROMPT (Soft-Core入り)
    - core_text     : OVV_CORE の文字列
    - external_text : OVV_EXTERNAL の文字列
    - context_key   : thread / channel ごとの ID
    - text          : ユーザーからのメッセージ
    - recent_mem    : 過去20件のメモリ
    - append_runtime_memory : メモリ保存関数
    - log_audit     : 監査ログ関数
    """

    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant", "content": core_text},
        {"role": "assistant", "content": external_text},
    ]

    # 過去ログを Ovv に注入
    for m in recent_mem[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )

        ans = res.choices[0].message.content.strip()

        append_runtime_memory(str(context_key), "assistant", ans)

        log_audit("assistant_reply", {
            "context_key": context_key,
            "length": len(ans)
        })

        return ans[:1900]

    except Exception as e:

        log_audit("openai_error", {
            "context_key": context_key,
            "error": repr(e)
        })

        return "Ovv との通信中にエラーが発生しました。"
