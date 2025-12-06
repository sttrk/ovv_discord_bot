# ovv/stabilizer.py
# Stabilizer (BIS Layer-3)
# - Ovv コアから返ってきた生テキストを「Discord 向け最終応答」に安定化するレイヤー
# - 役割：
#     1. [FINAL] セクション抽出
#     2. 空応答・None 防止（安全なフォールバック）
#     3. Discord 2000 文字制限を考慮した truncate
#
# ※ できるだけ後方互換になるように、関数名・クラス・インスタンスを複数公開する。

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Discord 制約を少し余裕を持って切る
MAX_DISCORD_LEN = 1900

# 共通フォールバックメッセージ
DEFAULT_FALLBACK_MESSAGE = (
    "Ovv コア処理中に予期しない状態が発生しました。"
    "少し時間をおいて再度お試しください。"
)


# ============================================================
# 内部ユーティリティ
# ============================================================

def _normalize(text: Optional[str]) -> str:
    """None / 空白を安全に正規化する."""
    if text is None:
        return ""
    # 念のため str() を通す
    return str(text).strip()


def _truncate_for_discord(text: str, max_len: int = MAX_DISCORD_LEN) -> str:
    """Discord の 2000 文字制限を考慮した truncate."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n...[truncated]"


def extract_final_section(raw: str) -> str:
    """
    Ovv からの生出力から [FINAL] セクションだけを抜き出す。

    振る舞い:
      1. [FINAL] があれば、その後ろのテキストをすべて返す。
      2. [FINAL] が無い場合は、全文をそのまま返す（後段で truncate）。
      3. 返り値は strip() 済みだが、空文字の可能性はある。
    """
    txt = _normalize(raw)
    if not txt:
        return ""

    # 1) [FINAL] がある場合 → 以降を抽出
    marker = "[FINAL]"
    if marker in txt:
        _, after = txt.split(marker, 1)
        return after.strip()

    # 2) 旧形式：[Stable] や [OUTPUT] に対応したい場合はここで追加してもよいが、
    #    現状は仕様上 [FINAL] 優先・無ければ全文とする。
    return txt.strip()


# 後方互換 alias（過去に extract_final_section / get_final 等で呼んでいる可能性に備える）
get_final = extract_final_section


# ============================================================
# Public API: Stabilizer 本体
# ============================================================

@dataclass
class Stabilizer:
    """
    BIS Layer-3 の安定化コンポーネント。

    想定される典型的な使用方法:

        from ovv.stabilizer import Stabilizer

        stabilizer = Stabilizer()
        raw = call_ovv(...)

        safe_text = stabilizer.stabilize(raw)
        await message.channel.send(safe_text)
    """

    max_len: int = MAX_DISCORD_LEN
    fallback_message: str = DEFAULT_FALLBACK_MESSAGE

    def stabilize(self, raw: Optional[str]) -> str:
        """
        生出力を Discord 向け最終テキストに変換する。

        手順:
          1. None / 空白 → フォールバック
          2. [FINAL] 抽出
          3. 抽出結果が空の場合 → フォールバック
          4. 文字数制限に合わせて truncate
        """
        # 1) None 安全化
        txt = _normalize(raw)

        # 完全に空なら即フォールバック
        if not txt:
            return self.fallback_message

        # 2) [FINAL] セクション抽出
        final_part = extract_final_section(txt)

        # 3) FINAL が空だった場合もフォールバック
        if not final_part:
            return self.fallback_message

        # 4) Discord 制限に合わせて truncate
        return _truncate_for_discord(final_part, self.max_len)

    # 関数的にも使えるように __call__ を alias
    def __call__(self, raw: Optional[str]) -> str:
        return self.stabilize(raw)


# ============================================================
# 関数スタイルのラッパー（後方互換用）
# ============================================================

# デフォルト設定の共有インスタンス
_default_stabilizer = Stabilizer()


def stabilize_ovv_output(
    raw: Optional[str],
    fallback_message: Optional[str] = None,
    max_len: Optional[int] = None,
) -> str:
    """
    関数スタイルの API。
    既存コードが `stabilize_ovv_output(raw_ans)` などで呼んでいても動くように用意。

    Args:
        raw: Ovv からの生出力
        fallback_message: カスタムフォールバック（省略時はデフォルト）
        max_len: カスタム最大長（省略時はデフォルト）

    Returns:
        Discord にそのまま送れる最終テキスト
    """
    if fallback_message is None and max_len is None:
        # 一番シンプルなパス：既定インスタンスに丸投げ
        return _default_stabilizer.stabilize(raw)

    # カスタム設定で一時インスタンスを作る
    st = Stabilizer(
        max_len=max_len or MAX_DISCORD_LEN,
        fallback_message=fallback_message or DEFAULT_FALLBACK_MESSAGE,
    )
    return st.stabilize(raw)


# 過去に "stabilize" / "stabilizer" という名前で import している可能性に備えた alias
stabilize = stabilize_ovv_output          # 関数として使う場合
stabilizer = _default_stabilizer          # インスタンスとして使う場合