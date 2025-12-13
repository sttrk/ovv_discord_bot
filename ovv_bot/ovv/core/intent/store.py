# ovv/intent/store.py
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List

from .types import Intent


_MAX_BUFFER = 10  # context ごとの保持数


class IntentStore:
    """
    揮発 Intent Buffer（即応用）。
    再起動で消えてよい。
    """

    def __init__(self) -> None:
        self._buffer: Dict[str, Deque[Intent]] = defaultdict(
            lambda: deque(maxlen=_MAX_BUFFER)
        )

    def push(self, intent: Intent) -> None:
        if not intent.context_key:
            return
        self._buffer[intent.context_key].append(intent)

    def list_recent(self, context_key: str) -> List[Intent]:
        if not context_key:
            return []
        return list(self._buffer.get(context_key, []))

    def clear(self, context_key: str) -> None:
        if context_key in self._buffer:
            self._buffer.pop(context_key, None)


# ---- Singleton（最小構成）----
intent_store = IntentStore()