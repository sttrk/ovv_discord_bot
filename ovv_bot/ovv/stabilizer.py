# ovv/stabilizer.py
# Stabilizer v1.0 (A5-Minimal)
#
# 役割:
#   - Ovv から返ってきた「生テキスト(raw_ans)」から、
#     Discord に返すための最終テキストを安定して抽出する。
#   - [FINAL] セクション抽出 / 長さ制限 / フォールバック方針を一本化する。
#
# BIS フロー上の位置:
#   B (Boundary_Gate: bot.py)
#   I (Interface_Box: interface_box.py)
#   Ovv (ovv_call.py)
#   S (Stabilizer: 本ファイル)
#
# 現在はシンプルな FINAL 抽出だが、将来的に:
#   - エラー時の定形文
#   - Markdown 整形
#   - 分割送信のポリシー
# などをここで一元管理する想定。

from typing import Optional


def stabilize_output(raw_ans: Optional[str]) -> str:
    """
    Ovv からの raw_ans を受け取り、Discord に返す最終テキストを決定する。

    挙動:
        1. raw_ans が None / 空文字 → 固定エラーメッセージを返す。
        2. "[FINAL]" を含む場合 → その後ろだけを取り出し、strip する。
        3. "[FINAL]" が無い場合 → 全文をそのまま使う。
        4. いずれも 1900 文字に truncate（Discord 2000 制限の安全マージン）。
    """
    if not raw_ans:
        return "Ovv コアからの応答が空でした。少し時間をおいて再度お試しください。"

    text = str(raw_ans)

    if "[FINAL]" in text:
        try:
            # 最初の [FINAL] 以降だけを取り出す
            text = text.split("[FINAL]", 1)[1].strip()
        except Exception:
            # 分割に失敗しても落とさず、そのまま使う
            text = text

    # Discord の 2000 制限を考慮して少し余裕を見て truncate
    if len(text) > 1900:
        text = text[:1900] + "\n...[truncated]"

    # 念のため、完全空になってしまった場合のフォールバック
    if not text.strip():
        return "Ovv の出力が空になりました。もう一度試してみてください。"

    return text