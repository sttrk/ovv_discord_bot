# ovv_call/call_ovv.py
import json
from typing import List


def build_messages(system_prompt: str, core: str, external: str, recent_mem: List[dict], user_text: str):
    """
    Ovv のメッセージ構造を生成する純関数。
    副作用なし。
    """
    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant", "content": core},
        {"role": "assistant", "content": external},
    ]

    # 過去メモリ（最大20）
    for m in recent_mem[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    msgs.append({"role": "user", "content": user_text})

    return msgs


def call_ovv(
    openai_client,
    system_prompt: str,
    core: str,
    external: str,
    recent_mem: List[dict],
    context_key: int,
    user_text: str,
    temperature: float = 0.7,
):
    """
    Ovv の LLM 呼び出しロジック。
    副作用（メモリ保存・audit_log）は発生させない。
    返すのは「アシスタントの返答文字列」だけ。
    """

    msgs = build_messages(system_prompt, core, external, recent_mem, user_text)

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=temperature,
        )

        ans = res.choices[0].message.content.strip()

        # Discord の最大長にトリム
        return ans[:1900]

    except Exception as e:
        # 呼び出し元で audit_log を書けるように例外を返す
        return None, e

    return ans
