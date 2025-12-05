# ovv/ovv_call.py
# Ovv Call Layer - September Stable Edition (FINAL-only Output)

from typing import List
from openai import OpenAI
from config import OPENAI_API_KEY

# ============================================================
# Core / External Loader
# ============================================================
from ovv.core_loader import load_core, load_external

OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

# ============================================================
# Soft-Core
# ============================================================
OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary; MUST NOT become over-strict.
2. MUST use Clarify only when ambiguity materially affects answer quality.
3. MUST avoid hallucination.
4. MUST respect scope boundaries.
5. SHOULD decompose → reconstruct for stability.
6. MUST NOT phase-mix.
7. MAY trigger CDC but sparingly.
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作するアシスタント「Ovv」です。

- ユーザー体験を最優先し、過度に厳密すぎる応答は避けてください。
- 不明点があっても、ユーザーが次のアクションを取りやすいように実用的な回答を返してください。
- 思考過程（ステップバイステップの推論やメタコメント）は内部でのみ行い、
  ユーザーには「最終的な答え（FINAL）」だけを自然な文章で返してください。
- 数値計算や手順を伴う問題では、内部で一度ていねいに検算した上で、
  結論だけを返してください（途中計算は表示しない）。

常に次の Ovv Soft-Core を前提に振る舞います：

{OVV_SOFT_CORE}

出力ルール：
- ユーザーへの返答は 1 つの完成されたメッセージとして返すこと。
- 「推論中」「PREP」「DRAFT」「THOUGHT」などのセクションを表示してはならない。
- デバッグ用タグ（[PREP] など）も表示してはならない。
- 必要に応じて箇条書きや見出しを使ってよいが、過度に長すぎる説明は避けること。
""".strip()

# ============================================================
# OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# call_ovv: メイン推論エントリポイント
# ============================================================
def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    """
    Ovv のメイン呼び出し。
    - context_key: スレッド or チャンネル単位のコンテキストキー
    - text: 今回ユーザーが送ったメッセージ
    - recent_mem: runtime_memory から取得した直近メッセージ群（role/content形式）
    戻り値は Discord にそのまま送信してよい「最終回答（FINAL のみ）」。
    """

    # ベースメッセージ
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    # 過去メモリを少なめに注入（コストと安定性のバランス）
    for m in recent_mem[-20:]:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not content:
            continue
        msgs.append({"role": role, "content": content})

    # 今回ユーザーメッセージ
    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        raw = res.choices[0].message.content.strip()

        # 念のため将来の拡張（[FINAL] 形式など）に備えて簡易パーサを入れておくが、
        # 現仕様では SYSTEM_PROMPT 上「FINAL だけ返す」前提なので基本は raw をそのまま使う。
        # もし [FINAL] を含む形式に将来変えた場合は、ここで切り出せる。
        final_text = raw

        marker = "[FINAL]"
        if marker in raw:
            # 例: [FINAL] 以降だけを抜き出す
            idx = raw.rfind(marker)
            final_text = raw[idx + len(marker):].strip()

        # Discord の 2000 文字制限を少し余裕を持ってカット
        return final_text[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        return "Ovv コア処理中にエラーが発生しました。"
