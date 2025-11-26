import os
import discord
from discord.ext import commands
from openai import OpenAI
from datetime import datetime

# =======================================
# Environment Variables
# =======================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")
if not OPENAI_API_KEY:
    raise RuntimeError("環境変数 OPENAI_API_KEY が設定されていません。")

# =======================================
# OpenAI Client
# =======================================
client = OpenAI(api_key=OPENAI_API_KEY)

# =======================================
# Bootstrap Load
# =======================================
def load_bootstrap() -> str:
    path = "bootstrap_ovv.txt"
    if not os.path.exists(path):
        raise RuntimeError(f"ブートストラップ {path} が見つかりません。Render にアップロードされているか確認してください。")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_SYSTEM_PROMPT = load_bootstrap()

# =======================================
# Context Memory (channel/thread separate)
# =======================================
MEMORY_LIMIT = 20
conversation_memory = {}  # {channel_id: [msg, msg, ...]}

def add_memory(channel_id: int, role: str, content: str):
    if channel_id not in conversation_memory:
        conversation_memory[channel_id] = []
    conversation_memory[channel_id].append({"role": role, "content": content})
    if len(conversation_memory[channel_id]) > MEMORY_LIMIT:
        conversation_memory[channel_id] = conversation_memory[channel_id][-MEMORY_LIMIT:]

def get_memory(channel_id: int):
    return conversation_memory.get(channel_id, [])

def is_ovv_channel(channel) -> bool:
    """
    自動応答対象：
    1. チャンネル名が ovv で始まる (#ovv, #ovv-dev, ...)
    2. スレッド → 親チャンネル名が ovv で始まる
    """
    if isinstance(channel, discord.TextChannel):
        return channel.name.startswith("ovv")

    if isinstance(channel, discord.Thread):
        if channel.parent and channel.parent.name.startswith("ovv"):
            return True

    return False

# =======================================
# OpenAI Call
# =======================================
def call_ovv(prompt: str, memory):
    messages = [{"role": "system", "content": OVV_SYSTEM_PROMPT}]
    messages.extend(memory)
    messages.append({"role": "user", "content": prompt})

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages,
        temperature=0.3,
    )

    return completion.choices[0].message.content.strip()

# =======================================
# Discord Bot Setup
# =======================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# =======================================
# Events
# =======================================
@bot.event
async def on_ready():
    print(f"[INFO] Logged in as {bot.user} ({bot.user.id})")
    print("[INFO] Ovv Discord Bot Ready.")

# =======================================
# Main auto-reply handler (3方式)
# =======================================
@bot.event
async def on_message(message: discord.Message):
    # bot自身 or DM は無視
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
        return

    # コマンド処理を先に通す
    await bot.process_commands(message)

    # 自動応答対象判定
    if not is_ovv_channel(message.channel):
        return

    # "!o" コマンド以外の通常メッセージも処理
    user_text = message.content.strip()

    # メモリ追加（user）
    add_memory(message.channel.id, "user", user_text)

    async with message.channel.typing():
        try:
            memory = get_memory(message.channel.id)
            answer = call_ovv(user_text, memory)
        except Exception as e:
            print(f"[ERROR] call_ovv failed: {e}")
            await message.channel.send("OVVとの通信中にエラーが発生しました。")
            return

    # メモリ追加（assistant）
    add_memory(message.channel.id, "assistant", answer)

    # Discordの制限に合わせて分割
    if len(answer) <= 1900:
        await message.channel.send(answer)
    else:
        buf = ""
        for line in answer.splitlines(True):
            if len(buf) + len(line) > 1900:
                await message.channel.send(buf)
                buf = line
            else:
                buf += line
        if buf:
            await message.channel.send(buf)

# =======================================
# "!o" コマンド（省略版）
# =======================================
@bot.command(name="o")
async def o_command(ctx: commands.Context, *, question: str):
    user_text = question
    add_memory(ctx.channel.id, "user", user_text)

    async with ctx.channel.typing():
        try:
            memory = get_memory(ctx.channel.id)
            answer = call_ovv(user_text, memory)
        except Exception as e:
            print(f"[ERROR] !o failed: {e}")
            await ctx.send("OVVとの通信中にエラーが発生しました。")
            return

    add_memory(ctx.channel.id, "assistant", answer)

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
# RUN
# =======================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
