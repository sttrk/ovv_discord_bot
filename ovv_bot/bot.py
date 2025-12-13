# bot.py
# ---------------------------------------------------------------------
# Discord Adapter Layer
#
# ROLE:
#   - Discord I/O のみを担当する最外郭レイヤ
#   - すべての業務ロジックは Boundary_Gate 以下に委譲
#
# RESPONSIBILITY TAGS:
#   [DISCORD_IO]   Discord イベント処理
#   [DELEGATE]     Boundary_Gate への完全委譲
#   [DEBUG]        起動時の環境可視化 / Debug Command Suite 登録
#   [OBSERVE]      デプロイ時デバッグ通知（Webhook）
#
# CONSTRAINTS:
#   - Core / WBS / Persist / Notion を直接触らない
#   - コマンド解釈は Boundary_Gate に任せる
#   - 観測系は失敗しても Bot を止めない
# ---------------------------------------------------------------------

import os
import sys
import discord
from discord.ext import commands

# ================================================================
# [DEBUG] Render 起動時：ディレクトリ構造 / sys.path を可視化
# ================================================================
print("=== PROJECT DIR TREE DUMP (from bot.py working directory) ===")
for root, dirs, files in os.walk(".", topdown=True):
    level = root.count(os.sep)
    indent = " " * 2 * level
    print(f"{indent}{root}/")
    for f in files:
        print(f"{indent}  {f}")
print("=== END TREE DUMP ===\n")

print("=== PYTHON SYSPATH (import root check) ===")
for p in sys.path:
    print(p)
print("=== END SYSPATH ===\n")

# ================================================================
# Discord Bot 初期化
# ================================================================
from ovv.bis.boundary_gate import handle_discord_input
from debug.debug_commands import register_debug_commands  # ★ 必須

# ★ デプロイ通知（観測専用）
from ovv.debug.deploy_notifier import notify_deploy_ok

intents = discord.Intents.default()
intents.message_content = True

# command_prefix を空にし、全入力を on_message で扱う
bot = commands.Bot(command_prefix="", intents=intents)

# ================================================================
# Debug Command Suite 登録
# ================================================================
register_debug_commands(bot)
print("[DEBUG] Debug Command Suite registered.")

# ================================================================
# Discord Events
# ================================================================
@bot.event
async def on_ready():
    """
    Bot が Discord に正常ログインしたタイミングで一度だけ呼ばれる。
    ここを「デプロイ成功」とみなして通知する。
    """
    print(f"[Discord] Bot logged in as {bot.user}")

    # ------------------------------------------------------------
    # [OBSERVE] Deploy OK Notification（Webhook）
    #   - 失敗しても Bot を止めない
    #   - Webhook 未設定時は何もしない
    # ------------------------------------------------------------
    try:
        notify_deploy_ok(
            checks={
                "discord_login": "OK",
                "debug_commands": "registered",
                "boundary_gate": "ready",
            }
        )
        print("[DEBUG] Deploy notification sent.")
    except Exception as e:
        # 観測系は絶対に Bot を止めない
        print("[DEBUG] Deploy notification failed (ignored):", repr(e))


@bot.event
async def on_message(message: discord.Message):
    """
    [DISCORD_IO]

    - Bot 自身の発言は無視
    - Debug Commands は discord.py Command System に流す
    - 通常入力は Boundary_Gate に完全委譲
    """
    if message.author.bot:
        return

    # ---- Debug / commands.py 系は必ず通す ----
    await bot.process_commands(message)

    # ---- 業務ロジックは Boundary_Gate に委譲 ----
    await handle_discord_input(message)


# ================================================================
# Entry Point
# ================================================================
def run(token: str):
    print("[Discord] starting bot.run()")
    bot.run(token)


# Render の Start Command が "python bot.py" の場合に備える
if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("[ERROR] DISCORD_BOT_TOKEN が設定されていません。")
    else:
        run(token)