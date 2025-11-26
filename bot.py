import os
import discord
from discord.ext import commands
from datetime import datetime
from typing import Optional, List, Dict
from openai import OpenAI
import json

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
# OpenAI Client
# =======================================
client = OpenAI(api_key=OPENAI_API_KEY)

# =======================================
# Load Ovv Bootstrap
# =======================================
def load_bootstrap() -> str:
    path = "bootstrap_ovv.txt"
    if not os.path.exists(path):
        raise RuntimeError("bootstrap_ovv.txt が存在しません。Render の永続領域に配置してください。")

    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_SYSTEM_PROMPT = load_bootstrap()


# =======================================
# Persistent Storage for Memory
# =======================================
MEMORY_DIR = "memory"  # Render 永続ディスクに作成される

if not os.path.exists(MEMORY_DIR):
    os.makedirs(MEMORY_DIR)


def memory_path(channel_id: int) -> str:
    return os.path.join(MEMORY_DIR, f"{channel_id}.json")


def load_memory(channel_id: int) -> List[Dict]:
    """チャンネルごとの保存済みログを読み込む"""
    path = memory_path(channel_id)

    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_memory(channel_id: int, messages: List[Dict]):
    """チャンネルごとのログを保存（最大20件）"""
    path = memory_path(channel_id)
    messages = messages[-20:]  # 最大20件に圧縮

    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


# =======================================
# ChatGPT Call
# =======================================
def call_ovv(prompt: str, channel_id: int) -> str:
    """
    チャンネル単位で additional_context を渡す方式
    """

    history = load_memory(channel_id)

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        additional_context=history,
        system=OVV_SYSTEM_PROMPT,
        max_output_tokens=800
    )

    answer = response.output_text.strip()

    # 履歴に追加（assistant / user 両方）
    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": answer})
    save_memory(channel_id, history)

    return answer


# =======================================
# Discord Bot (Command: !o)
# =======================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =======================================
# Utility: #ovv だけで動くか判定
# =======================================
TARGET_CHANNEL_NAME = "ovv"


def is_ovv_channel(channel: discord.abc.Messageable) -> bool:
    if isinstance(channel, discord.Thread):
        return channel.parent and channel.parent.name == TARGET_CHANNEL_NAME

    return channel.name == TARGET_CHANNEL_NAME


# =======================================
# Events
# =======================================
@bot.event
async def on_ready():
    print(f"[INFO] Logged in as {bot.user} (ID: {bot.user.id})")
    print("[INFO] Ovv Discord Bot is ready.")


# =======================================
# Command: !o   → Ovv に質問
# =======================================
@bot.command(name="o")
async def ovv_short(ctx: commands.Context, *, message: str):
    # #ovv 以外では動作禁止
    if not is_ovv_channel(ctx.channel):
        await ctx.send("このコマンドは #ovv チャンネル専用です。")
        return

    async with ctx.channel.typing():
        try:
            answer = call_ovv(message, ctx.channel.id)
        except Exception as e:
            print(f"[ERROR] call_ovv failed: {e}")
            await ctx.send("OVV との通信中にエラーが発生しました。")
            return

    # 2000文字制限対策
    if len(answer) <= 1900:
        await ctx.send(answer)
    else:
        buf = ""
        for line in answer.splitlines(True):
            if len(buf) + len(line) > 1900:
                await ctx.send(buf)
                buf = line
            else:
                buf += line
        if buf:
            await ctx.send(buf)


# =======================================
# Run
# =======================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
