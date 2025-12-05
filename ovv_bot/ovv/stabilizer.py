# ovv/stabilizer.py
# Ovv Stabilizer Layer v1.0
#
# 役割:
#  - Ovv コアから返ってきた生テキスト(raw)を、
#    Discord 送信用の安定したメッセージ列(List[str])に整形する。
#  - [FINAL] セクションの抽出
#  - Discord の 2000 文字制限への対応（約 1900 文字で分割）
#  - 空応答時のフォールバック
#
# 入力: raw (str)  … ovv_call.call_ovv の戻り値
# 出力: List[str]  … Discord の channel.send に順番に流せるテキスト

from typing import List


MAX_DISCORD_LEN = 1900  # 安全マージンを取った上限


def _extract_final(raw: str) -> str:
    """
    Ovv 出力から [FINAL] セクションを抽出する。
    - [FINAL] があれば、その後ろだけ採用
    - 無ければ全文を FINAL とみなす
    """
    if not raw:
        return ""

    if "[FINAL]" in raw:
        # 最初の [FINAL] 以降を切り出す
        body = raw.split("[FINAL]", 1)[1]
    else:
        body = raw

    return body.strip()


def _split_for_discord(text: str, max_len: int = MAX_DISCORD_LEN) -> List[str]:
    """
    Discord の文字数制限に収まるように text を分割する。
    - できるだけ改行単位で切る
    - それでも長い場合は強制的に文字数で切る
    """
    if len(text) <= max_len:
        return [text]

    chunks: List[str] = []
    remaining = text

    while len(remaining) > max_len:
        # 一旦 max_len 付近まで見て、直前の改行を探す
        cut = remaining.rfind("\n", 0, max_len)
        # 改行がほとんど見つからない場合は、強制的に max_len で切る
        if cut < max_len * 0.5:
            cut = max_len

        chunk = remaining[:cut].rstrip()
        chunks.append(chunk)
        remaining = remaining[cut:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


def stabilize_output(raw: str) -> List[str]:
    """
    Ovv コア出力(raw)を Stabilizer 規約に従って整形し、
    Discord に投げられる List[str] にして返す。
    """
    body = _extract_final(raw)

    if not body:
        # 完全空の場合のフォールバック
        fallback = (
            "Ovv から空の応答が返されました。\n"
            "一時的なエラーの可能性があります。もう一度入力してみてください。"
        )
        return _split_for_discord(fallback)

    return _split_for_discord(body)