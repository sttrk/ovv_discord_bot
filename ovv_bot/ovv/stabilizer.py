# ovv/stabilizer.py
# Stabilizer - BIS Architecture (B → I → Ovv → S)
#
# 役割:
#  - Ovv 生出力から [FINAL] セクションを抽出し、
#    Discord へ安全に返せる形に整える「出口箱」。

def stabilize_ovv_output(raw: str) -> str:
    """
    Ovv からの生出力を Discord へ安全に返せる形に整える。
    - [FINAL] セクション抽出
    - 空応答 / 異常応答のフォールバック
    - Discord 文字数制限の簡易ガード（1900 文字）
    """
    if not raw:
        return "Ovv からの応答が空でした。もう一度試してください。"

    text = raw

    # [FINAL] セクション抽出
    if "[FINAL]" in text:
        try:
            text = text.split("[FINAL]", 1)[1].strip()
        except Exception:
            # split 失敗時は元のテキストを使う
            text = text.strip()
    else:
        text = text.strip()

    if not text:
        text = "Ovv の応答を正しく解釈できませんでした。もう一度指示を送ってください。"

    # Discord 制限（2000 文字）に対する簡易保険
    if len(text) > 1900:
        text = text[:1900] + "\n...[truncated]"

    return text