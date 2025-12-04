# ovv/core_loader.py

import os
from typing import Optional

# このファイル (core_loader.py) が置かれているディレクトリ
BASE_DIR = os.path.dirname(__file__)

# コア定義ファイルのパス
CORE_PATH = os.path.join(BASE_DIR, "ovv_core.txt")
EXTERNAL_PATH = os.path.join(BASE_DIR, "ovv_external_contract.txt")

_core_cache: Optional[str] = None
_external_cache: Optional[str] = None


def _read_text(path: str) -> str:
    """
    与えられたパスから UTF-8 テキストを読み込む。
    失敗時はエラーメッセージ文字列を返す（クラッシュさせない方針）。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"[ERROR] Ovv core file not found: {path}"
    except Exception as e:
        return f"[ERROR] Failed to load {os.path.basename(path)}: {repr(e)}"


def load_core() -> str:
    """
    ovv_core.txt を読み込んで返す。
    初回のみディスクから読み込み、以降はメモリキャッシュを返す。
    """
    global _core_cache
    if _core_cache is None:
        _core_cache = _read_text(CORE_PATH)
    return _core_cache


def load_external() -> str:
    """
    ovv_external_contract.txt を読み込んで返す。
    初回のみディスクから読み込み、以降はメモリキャッシュを返す。
    """
    global _external_cache
    if _external_cache is None:
        _external_cache = _read_text(EXTERNAL_PATH)
    return _external_cache


def reload_all() -> None:
    """
    必要になった時に手動でコア定義を再読み込みしたい場合用。
    （現状 bot.py からは呼んでいないので任意）
    """
    global _core_cache, _external_cache
    _core_cache = _read_text(CORE_PATH)
    _external_cache = _read_text(EXTERNAL_PATH)
