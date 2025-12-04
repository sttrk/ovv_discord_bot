# debug/debug_commands.py

"""
Ovv Debug Commands

- !sql  : 簡易 SELECT 専用 psql
- !diag : 現在の context_key / mode / runtime_memory 長などを確認
"""

from typing import List

from discord.ext import commands

from database import pg as db_pg
from database.runtime_memory import load_runtime_memory
from brain.thread_brain import load_thread_brain
from ovv.mode import is_task_channel  # あなたが mode.py に実装した前提
from util.logger import log_info, log_error  # なければ print に差し替え可


def _get_context_key_from_ctx(ctx: commands.Context) -> int:
    """
    bot.py にある get_context_key と同じロジックをここにコピーする。
    divergence を避けるため、本体の実装と揃えておくこと。
    """
    msg = ctx.message
    ch = msg.channel
    if hasattr(ch, "parent") and getattr(ch, "parent", None) is not None and getattr(ch, "id", None):
        # Thread の場合
        if getattr(ch, "type", None).name == "public_thread":
            return ch.id
    if msg.guild is None:
        return msg.channel.id
    return (msg.guild.id << 32) | msg.channel.id


def setup_debug_commands(bot: commands.Bot):
    """
    bot.py 側から呼び出して、全 debug コマンドを登録するためのエントリポイント。
    """

    @bot.command(name="sql")
    async def sql(ctx: commands.Context, *, query: str):
        """
        簡易 psql：SELECT 文のみ許可。
        例: !sql SELECT id, event_type, created_at FROM ovv.audit_log ORDER BY id DESC LIMIT 10;
        """
        # 安全のため SELECT 以外は禁止
        q_strip = query.strip().lower()
        if not q_strip.startswith("select"):
            await ctx.send("現在は安全のため SELECT 文のみ許可しています。")
            return

        conn = db_pg.PG_CONN or db_pg.pg_connect()
        if conn is None:
            await ctx.send("PostgreSQL への接続がありません。")
            return

        try:
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()

            # 行数が多すぎる場合はカット
            max_rows = 20
            out_rows = rows[:max_rows]

            lines = []
            for r in out_rows:
                lines.append(" | ".join(str(x) for x in r))

            if len(rows) > max_rows:
                lines.append(f"... ({len(rows) - max_rows} rows truncated)")

            if not lines:
                await ctx.send("結果は 0 行でした。")
                return

            text = "```text\n" + "\n".join(lines)[:1900] + "\n```"
            await ctx.send(text)

        except Exception as e:
            log_error(f"[debug.sql] error: {repr(e)}")
            await ctx.send(f"SQL 実行中にエラーが発生しました: {type(e).__name__}")

    @bot.command(name="diag")
    async def diag(ctx: commands.Context):
        """
        現在スレッド／チャンネルの診断用。
        - context_key
        - mode (task / spot)
        - runtime_memory length
        - thread_brain の有無
        """
        ck = _get_context_key_from_ctx(ctx)
        session_id = str(ck)

        # モード判定（あなたの mode.py のロジックに依存）
        mode = "task" if is_task_channel(ctx.message) else "spot"

        mem = load_runtime_memory(session_id)
        mem_len = len(mem)

        summary = load_thread_brain(ck)
        has_brain = summary is not None

        text = (
            "【diag】\n"
            f"- context_key: {ck}\n"
            f"- mode:        {mode}\n"
            f"- runtime_mem: {mem_len} entries\n"
            f"- thread_brain:{'YES' if has_brain else 'NO'}\n"
        )

        await ctx.send(f"```text\n{text}\n```")

    # ここに今後 !logs, !brain_raw などを追加していける
    log_info("[debug] debug commands registered")