# ovv/bis/capture_interface_packet.py
# ============================================================
# MODULE CONTRACT: BIS / PacketCapture v1.5 (Defensive + Trace-Aware)
#
# ROLE:
#   - BIS パイプライン入口（Boundary_Gate → Interface_Box）で受け取る
#     InputPacket を "開発者向け診断用途" として 1 件だけ保持する。
#
# RESPONSIBILITY TAGS:
#   [CAPTURE]  パケットのスナップショットを安全に保持
#   [READ]     dbg_packet / debug_commands からの読み取り専用 API
#   [DEFENSE]  InputPacket 仕様変更に対して壊れない防御的構造
#   [TRACE]    trace_id を確実に観測可能な形で保持
#
# GUARANTEES:
#   - Capture は BIS パイプラインに影響しない（例外非送出）
#   - InputPacket の内部構造変更に対して壊れない
#   - JSON-safe 変換により Discord 表示で破綻しない
#   - 最大 1900 文字の安全トリミング
#
# NON-GOALS:
#   - Pipeline 制御（Interface_Box / Core / Stabilizer）への干渉
#   - Persist / Notion への書き込み
#   - ThreadBrain / RuntimeMemory 管理
# ============================================================

from __future__ import annotations

from typing import Optional, Any, Dict
import json


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

_MAX_DEBUG_LEN = 1900


# ------------------------------------------------------------
# Internal State
# ------------------------------------------------------------

_last_packet: Optional[Dict[str, Any]] = None


# ------------------------------------------------------------
# Utilities: JSON-safe conversion  [DEFENSE]
# ------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """
    InputPacket の構造が将来変化しても壊れないよう、
    JSON シリアライズ可能な形に安全変換する。

    - dict → 再帰処理
    - list/tuple → 再帰処理
    - 基本型 → そのまま
    - dataclass / object → __dict__ 展開
    - 未知オブジェクト → repr() 文字列に落とす
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    if hasattr(value, "__dict__"):
        return _json_safe(value.__dict__)

    return f"<unserializable {type(value).__name__}: {repr(value)}>"


def _extract_trace_id(packet: Any, safe_packet: Dict[str, Any]) -> None:
    """
    trace_id を可能な限り正規化して top-level に昇格させる。

    優先順位:
      1. packet.trace_id
      2. packet.meta["trace_id"]
    """
    try:
        tid = getattr(packet, "trace_id", None)
        if isinstance(tid, str) and tid:
            safe_packet["trace_id"] = tid
            return

        meta = getattr(packet, "meta", None)
        if isinstance(meta, dict):
            mt = meta.get("trace_id")
            if isinstance(mt, str) and mt:
                safe_packet["trace_id"] = mt
                return
    except Exception:
        pass


# ------------------------------------------------------------
# CAPTURE API  [CAPTURE]
# ------------------------------------------------------------

def capture(packet: Any) -> None:
    """
    Boundary_Gate から呼ばれ、直近の InputPacket を保持する。

    注意:
    - 例外をパイプラインへ伝播しない
    - Debug Layer は観測専用（挙動を変えない）
    """
    global _last_packet

    try:
        safe_packet = _json_safe(packet)

        # trace_id を正規化して保持
        if isinstance(safe_packet, dict):
            _extract_trace_id(packet, safe_packet)
            safe_packet["_captured_from"] = "Boundary_Gate"

        _last_packet = safe_packet

    except Exception as e:
        _last_packet = {
            "error": "capture_failed",
            "reason": repr(e),
        }


# ------------------------------------------------------------
# READ API  [READ]
# ------------------------------------------------------------

def get_last_interface_packet() -> Optional[Dict[str, Any]]:
    """
    Debug Layer（dbg_packet 等）が使用する読み取り API。
    """
    return _last_packet


# ------------------------------------------------------------
# DEBUG STRING API  [READ]
# ------------------------------------------------------------

def debug_dump() -> str:
    """
    Discord 表示用に整形された JSON を返す。

    Returns
    -------
    str
        Discord 制限を考慮し、最大 ~1900 文字で安全トリミング。
    """
    if _last_packet is None:
        return "(No packet captured)"

    try:
        text = json.dumps(_last_packet, indent=2, ensure_ascii=False)
        return text[:_MAX_DEBUG_LEN]
    except Exception as e:
        return f"(packet dump failed: {repr(e)})"