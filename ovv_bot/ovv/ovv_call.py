# ovv/ovv_call.py
# Ovv Call Layer - A5 Reasoning Upgrade v1

from typing import List, Optional
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External ローダ
from ovv.core_loader import load_core, load_external

# Thread Brain / Memory 参照用（循環依存なし）
import database.pg as db_pg


# ============================================================
# Core / External / Soft-Core
# ============================================================
OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary.
2. MUST use Clarify only when ambiguity materially affects answer quality.
3. MUST avoid hallucination.
4. MUST respect scope boundaries.
5. SHOULD decompose → reconstruct for stability.
6. MUST NOT phase-mix (設計・推論・出力を混ぜない)。
7. MAY trigger CDC (自己監査) but sparingly and構造化して扱う。
""".strip()


# ============================================================
# SYSTEM_PROMPT（A5: Final Only / Thread-Brain 対応）
# ============================================================
SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する「Ovv」です。

役割:
- ユーザーの継続タスクを支援し、必要に応じて過去文脈・Thread Brain を参照しながら回答します。
- UI 版 Ovv と同じ哲学 (Ovv Soft-Core) を保持しつつ、Discord 版として外部システムとの連携を前提とします。

Ovv Soft-Core:
{OVV_SOFT_CORE}

挙動上の重要ルール:
- あなたは「思考の構造化」は内部で行い、ユーザーには最終結論だけを返します。
- 内部思考や検討メモ、ドラフト、候補案をそのまま出力してはなりません。
- 出力は必ず次の形式に従ってください:

[FINAL]
<ユーザーが読むための最終回答だけを書く。日本語で、簡潔かつ具体的に。>

制約:
- ユーザーの業務・開発に直接役立つことを最優先とし、不要な雑談や冗長な前置きは避ける。
- Thread Brain に記録された high_level_goal / next_actions / history_digest は、
  「長期的な方針」や「これまでの合意事項」として尊重しつつ、
  矛盾があれば最新のユーザー発言を優先する。
- 実装コードに関する回答では、なるべく具体的な関数名・ファイル名・責務境界を明示する。
""".strip()


# ============================================================
# OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# Thread Brain -> テキスト変換（LLM に渡す要約）
# ============================================================
def _format_thread_brain(tb: dict) -> str:
    """
    thread_brain(JSON) を、LLM が使いやすい短いテキストに変換する。
    recent_messages 全部は渡さず、meta / status / goal / next_actions など要点だけ。
    """
    meta = tb.get("meta", {})
    status = tb.get("status", {})
    high_level_goal = tb.get("high_level_goal", "")
    history_digest = tb.get("history_digest", "")
    next_actions = tb.get("next_actions", [])
    unresolved = tb.get("unresolved", [])

    lines = []

    # Meta / Status
    phase = status.get("phase", "")
    last_event = status.get("last_major_event", "")

    if phase:
        lines.append(f"phase: {phase}")
    if last_event:
        lines.append(f"last_major_event: {last_event}")

    # Goal / Digest
    if high_level_goal:
        lines.append(f"high_level_goal: {high_level_goal}")
    if history_digest:
        lines.append(f"history_digest: {history_digest}")

    # Next actions
    if next_actions:
        lines.append("next_actions:")
        for a in next_actions[:5]:
            lines.append(f"- {a}")

    # Unresolved issues
    if unresolved:
        lines.append("unresolved:")
        for u in unresolved[:5]:
            if isinstance(u, str):
                lines.append(f"- {u}")
            elif isinstance(u, dict):
                title = u.get("title") or str(u)[:80]
                lines.append(f"- {title}")

    text = "\n".join(lines)
    if len(text) > 1200:
        text = text[:1200] + " ...[truncated]"

    return text or "(no thread_brain summary)"


# ============================================================
# call_ovv: A5 強化版
# ============================================================
def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    """
    A5: Ovv 推論呼び出し
    - Soft-Core / Core / External / Thread Brain / Runtime Memory を統合して LLM に渡す。
    - 返却値は生テキストだが、[FINAL] 付きで返すことを期待。
    """

    messages: List[dict] = []

    # 1) System + Soft-Core
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "assistant", "content": OVV_CORE})
    messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # 2) Thread Brain（あれば）
    try:
        tb = db_pg.load_thread_brain(context_key)
    except Exception as e:
        print("[call_ovv] thread_brain load error:", repr(e))
        tb = None

    if tb:
        tb_text = _format_thread_brain(tb)
        messages.append({
            "role": "system",
            "content": f"[ThreadBrain]\n{tb_text}"
        })

    # 3) 直近メモリ（PG runtime_memory）から 20 件まで
    for m in recent_mem[-20:]:
        role = m.get("role", "user")
        content = m.get("content", "")
        # 念のため空はスキップ
        if not content:
            continue
        messages.append({"role": role, "content": content})

    # 4) 今回ユーザー入力
    messages.append({"role": "user", "content": text})

    # 5) OpenAI 呼び出し
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.5,
        )
        raw = res.choices[0].message.content.strip()

        # runtime_memory に assistant 発話を保存
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] append_runtime_memory error:", repr(e))

        # そのまま返す（最終フィルタは bot.py 側で [FINAL] 抽出）
        return raw[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        try:
            db_pg.log_audit("openai_error", {
                "context_key": context_key,
                "user_text": text[:500],
                "error": repr(e),
            })
        except Exception:
            pass

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"
