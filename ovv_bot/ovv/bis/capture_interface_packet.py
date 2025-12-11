# ovv/bis/capture_interface_packet.py
# ============================================================
# MODULE CONTRACT: BIS / PacketCapture v1.4 (Defensive Edition)
#
# ROLE:
#   - BIS パイプライン入口（Boundary_Gate → Interface_Box）で受け取る
#     InputPacket を "開発者向け診断用途" として 1 件だけ保持する。
#
# RESPONSIBILITY TAGS:
#   [CAPTURE]  パケットのスナップショットを安全に保持
#   [READ]     dbg_packet / debug_commands からの読み取り専用 API
#   [DEFENSE]  InputPacket 仕様変更に対して壊れない防御的構造を採用
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
    - 基本型（str/int/bool/None など）→ そのまま
    - 未知オブジェクト → repr() 文字列に落とす
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]

    # 基本型はそのまま
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    # dataclass/object 対応
    if hasattr(value, "__dict__"):
        return _json_safe(value.__dict__)

    # それ以外の未知型は repr に落とす
    return f"<unserializable {type(value).__name__}: {repr(value)}>"


# ------------------------------------------------------------
# CAPTURE API  [CAPTURE]
# ------------------------------------------------------------

def capture(packet: Any) -> None:
    """
    Boundary_Gate から呼ばれ、直近の InputPacket を保持する。

    注意:
    - 例外をパイプラインへ伝播しない（Debug Layer は読み取り専用であるべき）。
    - InputPacket の構造が変化しても _json_safe により安全に保持される。
    """
    global _last_packet

    try:
        # dataclass / object / dict などすべて JSON-safe に変換
        safe_packet = _json_safe(packet)
        # レイヤ情報を添付（開発者が判別できるように）
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
        2000 文字制限を考慮し、~1900 文字で安全トリミングする。
    """
    if _last_packet is None:
        return "(No packet captured)"

    try:
        text = json.dumps(_last_packet, indent=2, ensure_ascii=False)
        return text[:1900]
    except Exception as e:
        return f"(packet dump failed: {repr(e)})"