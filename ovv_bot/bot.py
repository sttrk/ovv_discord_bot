# bot.py
# ---------------------------------------------------------------------
# Discord Adapter Layer
# ＋ Render環境のディレクトリ構造と sys.path をログに出力する仕組みを統合
# ---------------------------------------------------------------------

import os
import sys
import discord
from discord.ext import commands

# ================================================================
# 0.  Render起動時：ディレクトリツリー + sys.path をログ出力
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

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="", intents=intents)


@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    # BOT のメッセージは無視
    if message.author.bot:
        return

    # BoundaryGate に全て委譲
    await handle_discord_input(message)


def run(token: str):
    bot.run(token)


# Render の Start Command が "python bot.py" の場合、以下が必要。
# Python モジュールルートが正しく設定されていれば問題なし。
if __name__ == "__main__":
    # 必要に応じて環境変数から TOKEN を読み込む形でもOK
    token = os.getenv("DISCORD_BOT_TOKEN")
    if token:
        run(token)
    else:
        print("DISCORD_BOT_TOKEN が設定されていません。")