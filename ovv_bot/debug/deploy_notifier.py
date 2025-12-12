# ovv/debug/deploy_notifier.py
# ============================================================
# MODULE CONTRACT: Debug / DeployNotifier v1.0
#
# ROLE:
#   - Ovv のデプロイ・起動結果を Discord Webhook に通知する
#
# DESIGN PRINCIPLES:
#   - 観測専用（制御しない）
#   - 例外は絶対に raise しない
#   - BIS / Bot / Pipeline に影響を与えない
# ============================================================

from __future__ import annotations

import os
import json
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import urllib.request


# ------------------------------------------------------------
# Config
# ------------------------------------------------------------

WEBHOOK_URL = os.getenv("OVV_DEBUG_WEBHOOK_URL")
ENV_NAME = os.getenv("OVV_ENV", "unknown")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------
# Low-level sender (NO EXCEPTION)
# ------------------------------------------------------------

def _send_webhook(payload: Dict[str, Any]) -> None:
    """
    Discord Webhook へ JSON を POST する。
    例外は絶対に外へ出さない。
    """
    if not WEBHOOK_URL:
        # Webhook 未設定時は完全に黙る（仕様）
        return

    try:
        body = json.dumps(
            {
                # Discord Webhook は content or embeds を期待する
                "content": "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"
            },
            ensure_ascii=False,
        ).encode("utf-8")

        req = urllib.request.Request(
            WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=5):
            pass

    except Exception:
        # 観測系は絶対に失敗を伝播しない
        traceback.print_exc()


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def notify_deploy_ok(checks: Optional[Dict[str, str]] = None) -> None:
    """
    デプロイ成功通知。
    """
    payload = {
        "service": "Ovv",
        "event": "DEPLOY_OK",
        "environment": ENV_NAME,
        "timestamp": _now_iso(),
        "summary": "Ovv deployed successfully",
        "checks": checks or {},
    }
    _send_webhook(payload)


def notify_deploy_warn(
    *,
    fail_count: int,
    last_trace_id: Optional[str] = None,
    last_checkpoint: Optional[str] = None,
) -> None:
    """
    デプロイ後に警告がある場合の通知。
    """
    payload = {
        "service": "Ovv",
        "event": "DEPLOY_WARN",
        "environment": ENV_NAME,
        "timestamp": _now_iso(),
        "fail_count": fail_count,
        "last_trace_id": last_trace_id,
        "last_checkpoint": last_checkpoint,
    }
    _send_webhook(payload)