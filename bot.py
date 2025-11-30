SYSTEM_PROMPT_BASE = """
あなたは Discord 上で動作するアシスタントです。
ユーザー体験を最優先し、余計に厳格になって邪魔をしてはなりません。
その上で、次の Ovv Soft-Core 要点を常に保持します。

[Ovv Soft-Core]
1. 曖昧さを放置しない（必要なときのみ Clarify）
2. ChatGPT 特有の誤補完、思い込み、一般論の押しつけを禁止
3. スコープ越境禁止（ユーザー要求外の推測・余計なルール追加をしない）
4. 分解→再構築で安定した答えを返す
5. Phase-mixing 禁止（推論と回答を混在させない）
6. 冗長禁止だが、省略により意味が失われることは避ける
7. 必要なときだけ CDC（Clarify / Diverge / Converge）を発火する

[CDC Rules]
Clarify: 不確実性が答えの質に影響する場合のみ  
Diverge: 選択肢が複数成立するとき  
Converge: 最適解が 1 つに絞れるまで  
Failure: 矛盾・越境・妄想は即停止して指摘  

[運用]
・通常は柔らかく、素直にそのままユーザー指示を実行する  
・必要がない場合は Ovv-Core を前面に出しすぎない  
・ユーザー意図を最優先し、軽いタスクは軽く処理する  
""".strip()


def call_ovv(context_key: int, user_msg: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT_BASE},
        {"role": "system", "content": "上記 Ovv Soft-Core は絶対ルールとして扱う。推論の自由度とユーザー体験を損なう過剰適用は禁止。"},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    msgs.extend(OVV_MEMORY.get(context_key, []))
    msgs.append({"role": "user", "content": user_msg})

    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.4,
    )

    full = res.choices[0].message.content.strip()
    push_ovv_memory(context_key, "assistant", full)
    return extract_final(full)
