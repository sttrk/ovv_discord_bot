# ============================================================
# MODULE CONTRACT: BIS / Constraint Filter
# ROLE:
#   - Boundary → InterfaceBox → Core の処理ラインに乗る前に、
#     packet(dict) に対して安全性チェック・制約適用を行う。
#
# RESPONSIBILITY:
#   - packet(dict) の最低限の必須フィールド検証
#   - 型崩壊や None 値の補正（Safe Completion）
#   - Core に渡す前段階の Guard Layer
#
# INBOUND:
#   - BIS packet(dict)（capture_interface_packet で生成された値）
#
# OUTBOUND:
#   - 安全に補正された packet(dict)
#
# CONSTRAINT:
#   - BoundaryGate / Core への依存は禁止
#   - 外部サービス（Notion/PG）を触ってはならない
#   - 複雑ロジック禁止（A5-Minimal）
# ============================================================

from typing import Any, Dict


# ------------------------------------------------------------
# RESPONSIBILITY TAG: Packet Constraint Guard
# ------------------------------------------------------------
def apply_constraint_filter(packet: Dict[str, Any]) -> Dict[str, Any]:
    """
    A5-Minimal で必要な制約は非常にシンプル。
    - packet が dict であること
    - 必須キーが存在すること
    """

    if not isinstance(packet, dict):
        # dict でない場合、InterfaceBox と Core が壊れるため例外化
        raise ValueError("BIS ConstraintFilter: packet must be dict.")

    # command は None の場合がある（チャット本文だけのケース）
    # 最小仕様では補正しない（Core 側で判定可）
    if "command" not in packet:
        packet["command"] = None

    # content も最低限補正
    if "content" not in packet:
        packet["content"] = None

    # source が消えていたら discord で補正
    if "source" not in packet:
        packet["source"] = "discord"

    # raw は必ず残す
    if "raw" not in packet:
        packet["raw"] = packet

    return packet