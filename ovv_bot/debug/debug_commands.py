# debug/debug_commands.py
# ============================================================
# MODULE CONTRACT
#   NAME : debug_commands
#   LAYER: Gate-Assist (Debug Command Handler)
#
# ROLE:
#   - Discord からの debug コマンドを処理し、人間可読な内部状態を返す。
#   - Ovv 本体（BIS / Core / Persist / NotionOps）の安定性を監視する
#     「開発者用ダッシュボード」の役割を持つ。
#
# STABLE PUBLIC API:
#   - register_debug_commands(bot: commands.Bot) -> None
#
#     呼び出し側はこの関数だけを import / 呼び出しすればよい。
#     内部で提供される debug コマンド群は、Ovv の構造が変わっても
#     可能な限り互換性を維持する。
#
# CURRENT IMPLEMENTED COMMANDS (v1.0):
#   - !dbg_flow
#       BIS パイプライン（Boundary_Gate / Interface_Box / Core / Stabilizer /
#       Persist / NotionOps）が import 可能かどうかを静的に検査する。
#
# FUTURE COMMANDS (予定):
#   - !bs        : Boot Summary（環境・PG 接続など）
#   - !dbg_packet: InputPacket / InterfacePacket ダンプ
#   - !dbg_mem   : RuntimeMemory ダンプ
#   - !dbg_all   : TB + RuntimeMemory 統合ダンプ
#   - !wipe      : TB / RuntimeMemory のリセット
#
# CONSTRAINTS:
#   - Ovv-Core / Interface_Box / Stabilizer の実装を変更しても、
#     可能な限り本ファイルの public API（register_debug_commands）は変更しない。
#   - Debug Layer は「読み取り中心」とし、本番データの破壊的操作は
#     wipe 系コマンドのみに限定する（かつ明示的に実装する）。
#   - ThreadBrain 未実装フェーズでは、TB 依存のコマンドは実装しないか、
#     「未実装」である旨を返す。
# ============================================================

from __future__ import annotations

import importlib
from typing import List

import discord
from discord.ext import commands


# ============================================================
# Helper: モジュール存在チェック
# ============================================================

def _check_module(path: str) -> str:
    """
    importlib.import_module(path) を試み、その結果を文字列で返す。

    戻り値例:
      "OK"
      "ERROR: ImportError('xxx')"
    """
    try:
        importlib.import_module(path)
        return "OK"
    except Exception as e:
        return f"ERROR: {repr(e)}"


# ============================================================
# Public Entry
# ============================================================

def register_debug_commands(bot: commands.Bot) -> None:
    """
    デバッグコマンドをすべて登録する統一エントリポイント。

    呼び出し例（bot.py 側）:
        from debug.debug_commands import register_debug_commands
        register_debug_commands(bot)
    """

    # --------------------------------------------------------
    # !dbg_flow — BIS パイプライン静的チェック
    # --------------------------------------------------------
    @bot.command(name="dbg_flow")
    async def dbg_flow(ctx: commands.Context):
        """
        BIS パイプラインの「モジュール import 可否」を静的に検査する。

        チェック対象:
          - bot.py（Discord エントリ）
          - ovv.bis.boundary_gate
          - ovv.bis.interface_box
          - ovv.core.ovv_core
          - ovv.bis.stabilizer
          - database.pg
          - ovv.external_services.notion.ops.executor
        """

        checks = {
            "[GATE]   bot.py": "bot",
            "[BIS]    ovv.bis.boundary_gate": "ovv.bis.boundary_gate",
            "[BIS]    ovv.bis.interface_box": "ovv.bis.interface_box",
            "[CORE]   ovv.core.ovv_core": "ovv.core.ovv_core",
            "[BIS]    ovv.bis.stabilizer": "ovv.bis.stabilizer",
            "[PERSIST]database.pg": "database.pg",
            "[NOTION] ovv.external_services.notion.ops.executor":
                "ovv.external_services.notion.ops.executor",
        }

        lines: List[str] = []
        lines.append("=== BIS FLOW CHECK ===")
        lines.append("")

        for label, module_path in checks.items():
            result = _check_module(module_path)
            lines.append(f"{label:45} {result}")

        lines.append("")
        lines.append("※ ここがすべて OK であれば、")
        lines.append("  - Discord → Boundary_Gate → Interface_Box → Core → Stabilizer")
        lines.append("  - Persist / NotionOps")
        lines.append(" までの import 経路は壊れていないことを意味する。")
        lines.append("")
        lines.append("※ 実際の実行時エラーは Render Log / Console Log を確認すること。")

        text = "\n".join(lines)

        # Discord のメッセージ制限に合わせてコードブロックで返す
        await ctx.send(f"```\n{text}\n```")