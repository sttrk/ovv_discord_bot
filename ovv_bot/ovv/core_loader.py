import os
from typing import Optional

BASE_DIR = os.path.dirname(__file__)
DOCS_DIR = os.path.join(BASE_DIR, "docs")

CORE_PATH = os.path.join(DOCS_DIR, "ovv_core.txt")
EXTERNAL_PATH = os.path.join(DOCS_DIR, "ovv_external_contract.txt")

_core_cache: Optional[str] = None
_external_cache: Optional[str] = None


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"[ERROR] Ovv core file not found: {path}"
    except Exception as e:
        return f"[ERROR] Failed to load {os.path.basename(path)}: {repr(e)}"


def load_core() -> str:
    global _core_cache
    if _core_cache is None:
        _core_cache = _read_text(CORE_PATH)
    return _core_cache


def load_external() -> str:
    global _external_cache
    if _external_cache is None:
        _external_cache = _read_text(EXTERNAL_PATH)
    return _external_cache


def reload_all() -> None:
    global _core_cache, _external_cache
    _core_cache = _read_text(CORE_PATH)
    _external_cache = _read_text(EXTERNAL_PATH)