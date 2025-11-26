import os
import discord
from discord.ext import commands
from openai import OpenAI
from typing import Dict, List

# ============================================================
# Environment Variables
# ============================================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が未設定です。")

if not OPENAI_API_KEY:
    raise RuntimeError("環境変数 OPENAI_API_KEY が未設定です。")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# Load Core + External Contract
# ============================================================
def load_text(path: str) -> str:
    if not os.path.exists(path):
        raise RuntimeError(f"{path} が存在しません。")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_CORE = load_text("ovv_core.txt")
OVV_EXTERNAL = load_text("ovv_external_contract.txt")

SYSTEM_PROMPT = OVV_CORE + "\n\n" + OVV_EXTERNAL

# ============================================================
# Discord Bot Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ============================================================
# Memory store（チャンネル / スレッド単位）
# ============================================================
memory: Dict[int, List[Dict[str, str]]] = {}
MEMORY_LIMIT = 20

def get_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    return msg.channel.id

def append_message(key: int, role: str, content: str):
    memory.setdefault(key, [])
    memory[key].append({"role": role, "content": content})
    if len(memory[key]) > MEMORY_LIMIT:
        memory[key] = memory[key][-MEMORY_LIMIT:]

# ============================================================
# Call Ovv
# ============================================================
def call_ovv(key: int, prompt: str) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.extend(memory.get(key, []))
    msgs.append({"role": "user", "content": prompt})

    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.3,
    )
    return res.choices[0].message.content.strip()

# ============================================================
# 自然言語モード（!o不要 / ovv-◯◯限定）
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # ovv-◯◯ チャンネルのみ有効
    if isinstance(message.channel, discord.Thread):
        parent = message.channel.parent.name.lower()
        if not parent.startswith("ovv-"):
            return
    else:
        if not message.channel.name.lower().startswith("ovv-"):
            return

    key = get_key(message)
    append_message(key, "user", message.content)

    async with message.channel.typing():
        try:
            response = call_ovv(key, message.content)
        except Exception as e:
            print("[ERROR call_ovv]", e)
            await message.channel.send("Ovv との通信中にエラーが発生しました。")
            return

    append_message(key, "assistant", response)

    # 文字数制限
    if len(response) <= 1900:
        await message.channel.send(response)
    else:
        buf = ""
        for line in response.splitlines(True):
            if len(buf) + len(line) > 1900:
                await message.channel.send(buf)
                buf = line
            else:
                buf += line
        if buf:
            await message.channel.send(buf)

    # コマンド処理も通す
    await bot.process_commands(message)

# ============================================================
# !o — 明示コマンド
# ============================================================
@bot.command(name="o")
async def o_command(ctx: commands.Context, *, question: str):
    key = get_key(ctx.message)
    append_message(key, "user", question)

    async with ctx.channel.typing():
        response = call_ovv(key, question)

    append_message(key, "assistant", response)
    await ctx.send(response)

# ============================================================
# Run
# ============================================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
