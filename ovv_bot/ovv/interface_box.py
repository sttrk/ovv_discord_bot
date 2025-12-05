# ovv/interface_box.py
# Interface_Box v0.3 (Minimal)
#
# 役割:
#   - B(I)S の「I」に相当する前処理層。
#   - Ovv に渡す messages[] を一元的に構築する。
#   - A5-Minimal 向けに、必要最小限の整形のみ行う。
#
# ポリシー:
#   - Ovv Core / External / System Prompt は「文字列として」引数で受け取り、
#     ここからは絶対に import しない（循環依存防止）。
#   - Thread Brain は存在する場合のみ、短いテキストに整形して system として注入。
#   - recent_mem は最後の N 件だけを user/assistant として渡す。
#
# 依存:
#   - typing のみ（他の ovv モジュールに依存しない設計）

from typing import List, Dict, Optional


def _format_thread_brain(tb: Dict) -> str:
    """
    thread_brain(JSON) を、LLM が使いやすい短いテキストに変換する。
    - meta / status / goal / history_digest / next_actions / unresolved を要約。
    - 文字数はざっくり 1200 文字程度に切り詰める。
    """
    meta = tb.get("meta", {})
    status = tb.get("status", {})
    high_level_goal = tb.get("high_level_goal", "")
    history_digest = tb.get("history_digest", "")
    next_actions = tb.get("next_actions", [])
    unresolved = tb.get("unresolved", [])

    lines: List[str] = []

    phase = status.get("phase", "")
    last_event = status.get("last_major_event", "")

    if phase:
        lines.append(f"phase: {phase}")
    if last_event:
        lines.append(f"last_major_event: {last_event}")

    if high_level_goal:
        lines.append(f"high_level_goal: {high_level_goal}")
    if history_digest:
        lines.append(f"history_digest: {history_digest}")

    if next_actions:
        lines.append("next_actions:")
        for a in next_actions[:5]:
            lines.append(f"- {a}")

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


def build_messages(
    context_key: int,
    user_text: str,
    recent_mem: List[Dict],
    system_prompt: str,
    ovv_core: str,
    ovv_external: str,
    thread_brain: Optional[Dict] = None,
) -> List[Dict]:
    """
    Ovv に渡す messages[] を組み立てる唯一の入り口。

    入力:
      - context_key: スレッド／チャンネル単位の key（ログ用）
      - user_text:   今回のユーザ入力（生テキスト）
      - recent_mem:  runtime_memory から取り出した直近メモリ（List[dict]）
      - system_prompt: SYSTEM_PROMPT（Ovv Soft-Core + Discord 仕様）
      - ovv_core:      OVV_CORE（哲学・コア仕様）
      - ovv_external:  OVV_EXTERNAL（External Contract v1.4.x）
      - thread_brain:  あれば thread_brain(JSON)、なければ None

    出力:
      - OpenAI Chat Completions にそのまま渡せる messages[list]
    """

    messages: List[Dict] = []

    # 1) System + Soft-Core / Core / External
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if ovv_core:
        # Core spec は「過去の assistant 発話」として注入
        messages.append({"role": "assistant", "content": ovv_core})

    if ovv_external:
        # External Contract も assistant 扱いで注入
        messages.append({"role": "assistant", "content": ovv_external})

    # 2) Thread Brain（あれば system で injection）
    if thread_brain:
        tb_text = _format_thread_brain(thread_brain)
        messages.append(
            {
                "role": "system",
                "content": f"[ThreadBrain]\n{tb_text}",
            }
        )

    # 3) 直近メモリ（runtime_memory）から最大 20 件
    for m in recent_mem[-20:]:
        role = m.get("role", "user")
        content = m.get("content", "")

        if not content:
            continue

        # role が壊れていた場合は user として扱う
        if role not in ("user", "assistant", "system"):
            role = "user"

        messages.append({"role": role, "content": content})

    # 4) 今回のユーザ入力（必ず最後に入れる）
    messages.append({"role": "user", "content": user_text})

    return messages