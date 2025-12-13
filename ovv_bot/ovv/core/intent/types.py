# ovv/intent/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime
import uuid


@dataclass
class Intent:
    """
    ユーザーの「やりたいこと」の最小表現。

    NOTE:
      - 推論結果ではない
      - draft → accepted → promoted の状態遷移のみ
    """
    intent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    context_key: str = ""
    raw_text: str = ""

    state: str = "draft"  # draft | accepted | promoted | dropped

    created_at: datetime = field(default_factory=datetime.utcnow)

    meta: Dict[str, Any] = field(default_factory=dict)