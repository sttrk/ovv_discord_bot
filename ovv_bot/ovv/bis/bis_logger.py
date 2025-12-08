# ovv/bis/bis_logger.py
"""
[MODULE CONTRACT]
NAME: bis_logger
ROLE: BIS Internal Logger (Layer: ALL)

PURPOSE:
    - BIS 4 Layer (GATE / IFACE / CORE / STAB / PERSIST) のログを統一形式で出力する
    - レイヤ境界でのエラー位置を即特定できるようにする
    - print() 直接使用は原則禁止。すべてこの logger を経由する。

MUST:
    - レイヤ名とメッセージだけで簡潔にログを出す
    - Render / ローカル双方で読める形式
    - 例外発生時は logger.error() を使う

FORMAT:
    [BIS:<LAYER>] message="..." ctx=xxx
"""

import sys
import traceback
from typing import Optional


def _emit(layer: str, message: str, ctx: Optional[str] = None):
    prefix = f"[BIS:{layer}]"
    if ctx:
        print(f"{prefix} ctx={ctx} message={message}")
    else:
        print(f"{prefix} message={message}")


def gate(msg: str, ctx: Optional[str] = None):
    _emit("GATE", msg, ctx)


def iface(msg: str, ctx: Optional[str] = None):
    _emit("IFACE", msg, ctx)


def core(msg: str, ctx: Optional[str] = None):
    _emit("CORE", msg, ctx)


def stab(msg: str, ctx: Optional[str] = None):
    _emit("STAB", msg, ctx)


def persist(msg: str, ctx: Optional[str] = None):
    _emit("PERSIST", msg, ctx)


def error(layer: str, err: Exception, ctx: Optional[str] = None):
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    prefix = f"[BIS:{layer}:ERROR]"
    if ctx:
        print(f"{prefix} ctx={ctx} error={repr(err)}\n{tb}")
    else:
        print(f"{prefix} error={repr(err)}\n{tb}")