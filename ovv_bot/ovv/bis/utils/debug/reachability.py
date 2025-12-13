# ovv/bis/utils/debug/reachability.py
from __future__ import annotations

import importlib
from typing import Dict


def check_packet_reachability() -> Dict[str, str]:
    """
    パケットが到達可能なレイヤを import ベースで確認する。
    実行はしない。
    """
    checks = {
        "Boundary_Gate": "ovv.bis.boundary_gate",
        "Interface_Box": "ovv.bis.interface_box",
        "Core": "ovv.core.ovv_core",
        "Stabilizer": "ovv.bis.stabilizer",
        "Persist(PG)": "database.pg",
    }

    result: Dict[str, str] = {}

    for label, module_path in checks.items():
        try:
            importlib.import_module(module_path)
            result[label] = "READY"
        except Exception:
            result[label] = "NOT_READY"

    return result