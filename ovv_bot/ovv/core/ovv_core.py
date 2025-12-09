# ovv/core/ovv_core.py

def run_ovv_core(packet):
    """
    Core の最小実装。
    InterfaceBox から受け取った packet をそのまま返すだけのプレースホルダ。
    後で router や pipeline を実装する。
    """
    return {
        "status": "ok",
        "type": "core_placeholder",
        "payload": packet
    }
