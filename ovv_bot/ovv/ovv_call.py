# ovv/ovv_call.py
# Ovv Call Layer - A5 / BIS 対応版
#
# 役割:
#   - Interface_Box から渡された InputPacket をもとに、
#     Ovv Core（コア哲学）＋ External Contract ＋ ThreadBrain ＋ state_hint を統合して LLM を叩く。
#   - LLM からの生テキストをそのまま返す（[FINAL] 抽出は Stabilizer 側の責務）。
#
#   呼び出し元:
#     bot.py → call_ovv(context_key, input_packet)

from typing import List, Dict, Any

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External ローダ
from ovv.core_loader import load_core, load_external

# Runtime Memory / Audit 用
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
6. MUST NOT phase-mix（設計・推論・出力を混ぜない）。
7. MAY trigger CDC（自己監査） but sparingly and 構造化して扱う。
""".strip()


# ============================================================
# SYSTEM_PROMPT（A5: Final Only / TB & BIS 対応）
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
# state_hint / TB を system メッセージ用テキストに変換
# ============================================================
def _format_state_hint(state_hint: Dict[str, Any]) -> str:
    """
    state_hint(dict) を LLM に渡すための短いテキストに変換。
    例:
      {
        "mode": "task",
        "context_key": 123,
        "reason": "task_channel",
      }
    """
    if not state_hint:
        return ""

    lines: List[str] = ["[StateHint]"]
    mode = state_hint.get("mode")
    if mode:
        lines.append(f"- mode: {mode}")

    context_key = state_hint.get("context_key")
    if context_key is not None:
        lines.append(f"- context_key: {context_key}")

    # その他のフィールドもざっくり列挙
    for k, v in state_hint.items():
        if k in ("mode", "context_key"):
            continue
        lines.append(f"- {k}: {v}")

    return "\n".join(lines)


def _inject_context_system_messages(
    messages: List[Dict[str, str]],
    input_packet: Dict[str, Any],
) -> None:
    """
    InputPacket 内の thread_brain / tb_prompt / tb_scoring / state_hint を
    system ロールとして messages に注入する。
    """

    tb_prompt: str = input_packet.get("tb_prompt") or ""
    tb_scoring: str = input_packet.get("tb_scoring") or ""
    state_hint: Dict[str, Any] = input_packet.get("state_hint") or {}

    # state_hint
    state_text = _format_state_hint(state_hint)
    if state_text:
        messages.append({"role": "system", "content": state_text})

    # ThreadBrain 要約
    if tb_prompt:
        messages.append({"role": "system", "content": f"[ThreadBrain]\n{tb_prompt}"})

    # TB-Scoring（優先ルール）
    if tb_scoring:
        messages.append({"role": "system", "content": tb_scoring})


# ============================================================
# call_ovv: A5 / BIS 強化版
# ============================================================
def call_ovv(context_key: int, input_packet: Dict[str, Any]) -> str:
    """
    A5 / BIS: Ovv 推論呼び出し

    Parameters
    ----------
    context_key : int
        Discord 上の文脈キー（guild/channel/thread をまとめた ID）。
    input_packet : dict
        Interface_Box が構築した InputPacket。
        必須キー:
          - "user_text": str
          - "runtime_memory": List[dict]
        任意キー:
          - "thread_brain": Optional[dict]
          - "tb_prompt": str
          - "tb_scoring": str
          - "state_hint": Optional[dict]

    Returns
    -------
    str
        Ovv からの生テキスト（[FINAL] を含む想定）。
        [FINAL] 抽出は Stabilizer 側の責務。
    """

    user_text: str = input_packet.get("user_text", "") or ""
    recent_mem: List[Dict[str, Any]] = input_packet.get("runtime_memory") or []

    messages: List[Dict[str, str]] = []

    # 1) System + Soft-Core
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "assistant", "content": OVV_CORE})
    messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # 2) ThreadBrain / TB-Scoring / state_hint
    _inject_context_system_messages(messages, input_packet)

    # 3) 直近メモリ（PG runtime_memory）から 20 件まで
    for m in recent_mem[-20:]:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not content:
            continue
        # Discord / Ovv 用に role は user / assistant のみ想定
        if role not in ("user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": content})

    # 4) 今回ユーザー入力
    if user_text:
        messages.append({"role": "user", "content": user_text})
    else:
        # 万が一空なら、最低限のテキストを入れておく
        messages.append({"role": "user", "content": "（入力テキストが空でした。安全な応答を返してください。）"})

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

        # そのまま返す（最終フィルタは Stabilizer 側で [FINAL] 抽出）
        return raw[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        try:
            db_pg.log_audit("openai_error", {
                "context_key": context_key,
                "input_packet": {
                    "user_text": user_text[:200],
                    "has_thread_brain": bool(input_packet.get("thread_brain")),
                    "has_state_hint": bool(input_packet.get("state_hint")),
                },
                "error": repr(e),
            })
        except Exception:
            pass

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"