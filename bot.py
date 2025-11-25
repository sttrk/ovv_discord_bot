import os
import discord
from discord.ext import commands
from datetime import datetime
from typing import Optional
from openai import OpenAI

# =======================================
# Environment Variables
# =======================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if DISCORD_BOT_TOKEN is None:
    raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")

if OPENAI_API_KEY is None:
    raise RuntimeError("環境変数 OPENAI_API_KEY が設定されていません。")

# =======================================
# Bootstrapping
# =======================================
def load_bootstrap() -> str:
    path = "bootstrap_ovv.txt"
    if not os.path.exists(path):
        raise RuntimeError(f"ブートストラップファイル {path} が存在しません。")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_SYSTEM_PROMPT = load_bootstrap()

# =======================================
# OpenAI Client
# =======================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)

def call_ovv(prompt: str, mode: str = "general") -> str:
    messages = [
        {"role": "system", "content": OVV_SYSTEM_PROMPT},
        {"role": "user", "content": f"[MODE={mode}]\n{prompt}"}
    ]
    completion = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages,
        temperature=0.3,
    )
    return completion.choices[0].message.content.strip()

# =======================================
# Discord Bot
# =======================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# OVV 専用チャンネル ID の設定
OVV_CHANNEL_ID = 1442797863145967616  # ← 後で実際のチャンネル ID に置き換えてください

# =======================================
# Events
# =======================================
@bot.event
async def on_ready():
    print(f"[INFO] Logged in as {bot.user}")
    print("[INFO] Ovv Discord Bot is ready.")

# =======================================
# OVV 自動応答（チャンネル＋スレッド対応）
# =======================================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)  # コマンド処理を先に実行

    # 通常チャンネル or スレッド判定
    parent_id = getattr(message.channel, "parent_id", None)

    is_main = message.channel.id == OVV_CHANNEL_ID
    is_thread = parent_id == OVV_CHANNEL_ID

    if not (is_main or is_thread):
        return  # OVV チャンネル以外は OFF

    # 先頭が "!" のメッセージはコマンド扱いなので無視
    if message.content.startswith("!"):
        return

    # OVV 自動応答
    async with message.channel.typing():
        try:
            answer = call_ovv(message.content)
        except Exception as e:
            print(f"[ERROR] call_ovv failed: {e}")
            await message.channel.send("OVV との通信中にエラーが発生しました。")
            return

    await message.channel.send(answer)

# =======================================
# コマンド定義 (!o = ovv)
# =======================================
@bot.command(name="o")  # ← !o で反応
async def ovv_short(ctx: commands.Context, *, question: str):
    async with ctx.channel.typing():
        try:
            answer = call_ovv(question)
        except Exception as e:
            print(f"[ERROR] call_ovv failed: {e}")
            await ctx.send("OVV との通信中にエラーが発生しました。")
            return

    await ctx.send(answer)

@bot.command(name="help")
async def help_command(ctx: commands.Context):
    txt = (
        "OVV Bot コマンド一覧\n"
        "```text\n"
        "!o <質問内容>   : OVV に簡易質問\n"
        "通常メッセージ : OVV がチャンネル or スレッドで自動応答\n"
        "```"
    )
    await ctx.send(txt)

# =======================================
# Run
# =======================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
