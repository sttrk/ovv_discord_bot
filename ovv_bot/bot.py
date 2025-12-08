# bot.py
# ---------------------------------------------------------------------
# Discord Adapter Layer
# Discord からの入力だけを受け取り、boundary_gate に丸投げする。
# ---------------------------------------------------------------------

import discord
from discord.ext import commands
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