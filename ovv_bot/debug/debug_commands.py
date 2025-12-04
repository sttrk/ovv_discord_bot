# debug/debug_commands.py
# Debug Command Suite v1.0 - Implementation (Phase A/B-part)

import os
import importlib

from .debug_context import debug_context


# Utility: return simple debug text
def _msg(text: str) -> str:
    return f"[DEBUG] {text}"


# ============================================================
# A. Boot / Env / Config
# ============================================================

async def dbg_ping(message, args):
    return _msg("pong")


async def dbg_env(message, args):
    # 環境変数が「設定されているかだけ」を確認（値は出さない）
    keys = [
        "DISCORD_BOT_TOKEN",
        "OPENAI_API_KEY",
        "NOTION_API_KEY",
        "NOTION_TASKS_DB_ID",
        "NOTION_SESSIONS_DB_ID",
        "NOTION_LOGS_DB_ID",
        "POSTGRES_URL",
    ]
    lines = []
    for k in keys:
        v = os.getenv(k)
        status = "SET" if v else "MISSING"
        lines.append(f"{k}: {status}")
    joined = "\n".join(lines)
    return _msg("env check:\n" + joined)


async def dbg_cfg(message, args):
    # debug_context に依存が正しく注入されているかを確認
    checks = {
        "pg_conn": bool(debug_context.pg_conn),
        "notion": bool(debug_context.notion),
        "openai_client": bool(debug_context.openai_client),
        "load_mem": bool(debug_context.load_mem),
        "save_mem": bool(debug_context.save_mem),
        "append_mem": bool(debug_context.append_mem),
        "brain_gen": bool(debug_context.brain_gen),
        "brain_load": bool(debug_context.brain_load),
        "brain_save": bool(debug_context.brain_save),
        "ovv_core": bool(debug_context.ovv_core),
        "ovv_external": bool(debug_context.ovv_external),
        "system_prompt": bool(debug_context.system_prompt),
    }
    lines = [f"{k}: {'OK' if v else 'NONE'}" for k, v in checks.items()]
    return _msg("config check:\n" + "\n".join(lines))


async def dbg_boot(message, args):
    # env + cfg の要約を簡易的に返す
    env_ok = all(os.getenv(k) for k in [
        "DISCORD_BOT_TOKEN",
        "OPENAI_API_KEY",
        "NOTION_API_KEY",
        "NOTION_TASKS_DB_ID",
        "NOTION_SESSIONS_DB_ID",
        "NOTION_LOGS_DB_ID",
        "POSTGRES_URL",
    ])
    cfg_ok = all([
        debug_context.pg_conn,
        debug_context.notion,
        debug_context.openai_client,
        debug_context.load_mem,
        debug_context.brain_gen,
        debug_context.ovv_core,
        debug_context.ovv_external,
        debug_context.system_prompt,
    ])
    return _msg(f"boot summary: env_ok={env_ok}, cfg_ok={cfg_ok}")


# ============================================================
# B. Import / Module / Dependency
# ============================================================

async def dbg_import(message, args):
    if not args:
        return _msg("usage: !dbg import <module>")
    module_name = args[0]
    try:
        importlib.import_module(module_name)
        return _msg(f"import OK: {module_name}")
    except Exception as e:
        return _msg(f"import FAIL: {module_name} :: {repr(e)}")


async def dbg_file(message, args):
    if not args:
        return _msg("usage: !dbg file <path>")
    file_path = args[0]
    exists = os.path.exists(file_path)
    kind = "dir" if os.path.isdir(file_path) else "file"
    return _msg(f"{kind} exists={exists}: {file_path}")


async def dbg_load_notion(message, args):
    return _msg("load notion: TODO")


async def dbg_load_pg(message, args):
    return _msg("load pg: TODO")


async def dbg_load_core(message, args):
    return _msg("load core: TODO")


# ============================================================
# C. PostgreSQL Audit
# ============================================================

# ============================================================
# C. PostgreSQL Audit（実装版）
# ============================================================

async def dbg_pg_connect(message):
    try:
        conn = debug_context.pg_conn
        if conn is None:
            return "[DEBUG] PG: No connection"
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            row = cur.fetchone()
        return f"[DEBUG] PG connect OK: {row}"
    except Exception as e:
        return f"[DEBUG] PG connect FAIL: {repr(e)}"


async def dbg_pg_tables(message):
    try:
        conn = debug_context.pg_conn
        if conn is None:
            return "[DEBUG] PG: No connection"

        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema = 'ovv'
            """)
            rows = cur.fetchall()

        if not rows:
            return "[DEBUG] PG tables: none"
        txt = ", ".join([f"{r[0]}.{r[1]}" for r in rows])
        return f"[DEBUG] PG tables: {txt}"

    except Exception as e:
        return f"[DEBUG] PG tables FAIL: {repr(e)}"


async def dbg_pg_write(message):
    """
    audit_log にテスト書き込み
    """
    try:
        conn = debug_context.pg_conn
        if conn is None:
            return "[DEBUG] PG: No connection"

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ovv.audit_log (event_type, details)
                VALUES ('debug_test', '{"msg":"hello"}')
            """)
        return "[DEBUG] PG write OK"
    except Exception as e:
        return f"[DEBUG] PG write FAIL: {repr(e)}"


async def dbg_pg_read(message):
    """
    audit_log の最新 5 件を読む
    """
    try:
        conn = debug_context.pg_conn
        if conn is None:
            return "[DEBUG] PG: No connection"

        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, event_type, created_at
                FROM ovv.audit_log
                ORDER BY id DESC
                LIMIT 5
            """)
            rows = cur.fetchall()

        if not rows:
            return "[DEBUG] PG read: no logs"

        text = "\n".join([f"{r[0]} | {r[1]} | {r[2]}" for r in rows])
        return f"[DEBUG] PG read:\n{text}"

    except Exception as e:
        return f"[DEBUG] PG read FAIL: {repr(e)}"

# ============================================================
# D. Notion Audit
# ============================================================

# ============================================================
# D. Notion Audit（実装版）
# ============================================================

async def dbg_notion_auth(message):
    try:
        notion = debug_context.notion
        user = notion.users.list()
        return f"[DEBUG] Notion auth OK: {len(user.get('results', []))} users"
    except Exception as e:
        return f"[DEBUG] Notion auth FAIL: {repr(e)}"


async def dbg_notion_list(message):
    """
    tasks DB の先頭 3 件だけ読む簡易チェック
    """
    try:
        notion = debug_context.notion
        db_id = NOTION_TASKS_DB_ID

        q = notion.databases.query(
            **{
                "database_id": db_id,
                "page_size": 3
            }
        )
        count = len(q.get("results", []))
        return f"[DEBUG] Notion list OK: {count} rows"
    except Exception as e:
        return f"[DEBUG] Notion list FAIL: {repr(e)}"

# ============================================================
# E. Ovv Core / LLM Audit
# ============================================================

async def dbg_ovv_ping(message, args):
    return _msg("ovv ping: TODO")


async def dbg_ovv_core(message, args):
    return _msg("ovv core load: TODO")


async def dbg_ovv_llm(message, args):
    return _msg("ovv llm test: TODO")


# ============================================================
# F. Memory / Thread Brain Audit
# ============================================================

# ============================================================
# F. Memory / Thread Brain Audit（実装版）
# ============================================================

async def dbg_mem_load(message):
    try:
        key = str(message.channel.id)
        mem = debug_context.load_mem(key)
        return f"[DEBUG] mem_load OK: {len(mem)} records"
    except Exception as e:
        return f"[DEBUG] mem_load FAIL: {repr(e)}"


async def dbg_mem_write(message):
    try:
        key = str(message.channel.id)
        debug_context.append_mem(key, "debug", "hello_memory")
        return "[DEBUG] mem_write OK"
    except Exception as e:
        return f"[DEBUG] mem_write FAIL: {repr(e)}"


async def dbg_brain_gen(message):
    try:
        key = message.channel.id
        mem = debug_context.load_mem(str(key))
        br = debug_context.brain_gen(key, mem)
        if not br:
            return "[DEBUG] brain_gen FAIL"
        debug_context.brain_save(key, br)
        return "[DEBUG] brain_gen OK"
    except Exception as e:
        return f"[DEBUG] brain_gen EXCEPTION: {repr(e)}"


async def dbg_brain_show(message):
    try:
        key = message.channel.id
        br = debug_context.brain_load(key)
        if not br:
            return "[DEBUG] brain_show: none"
        short = json.dumps(br, ensure_ascii=False)[:1500]
        return f"[DEBUG] brain_show:\n{short}"
    except Exception as e:
        return f"[DEBUG] brain_show FAIL: {repr(e)}"

# ============================================================
# G. Routing / Event Audit
# ============================================================

async def dbg_route(message, args):
    return _msg("route OK")


async def dbg_event(message, args):
    return _msg("event OK (passed through on_message)")


async def dbg_chain(message, args):
    return _msg("chain test: TODO")


# ============================================================
# Dispatcher
# ============================================================

async def run_debug_command(message, cmd: str, args: list):

    # A. Boot / Env / Config
    if cmd == "ping":        return await dbg_ping(message, args)
    if cmd == "env":         return await dbg_env(message, args)
    if cmd == "cfg":         return await dbg_cfg(message, args)
    if cmd == "boot":        return await dbg_boot(message, args)

    # B. Import / Module / Dependency
    if cmd == "import":      return await dbg_import(message, args)
    if cmd == "file":        return await dbg_file(message, args)
    if cmd == "load_notion": return await dbg_load_notion(message, args)
    if cmd == "load_pg":     return await dbg_load_pg(message, args)
    if cmd == "load_core":   return await dbg_load_core(message, args)

    # C. PostgreSQL
    if cmd == "pg_connect": return await dbg_pg_connect(message)
    if cmd == "pg_tables": return await dbg_pg_tables(message)
    if cmd == "pg_write": return await dbg_pg_write(message)
    if cmd == "pg_read": return await dbg_pg_read(message)

    # D. Notion
    if cmd == "notion_auth": return await dbg_notion_auth(message)
    if cmd == "notion_list": return await dbg_notion_list(message)
   
    # E. Ovv Core / LLM
    if cmd == "ovv_ping":    return await dbg_ovv_ping(message, args)
    if cmd == "ovv_core":    return await dbg_ovv_core(message, args)
    if cmd == "ovv_llm":     return await dbg_ovv_llm(message, args)

    # F. Memory / Brain
    if cmd == "mem_load": return await dbg_mem_load(message)
    if cmd == "mem_write": return await dbg_mem_write(message)
    if cmd == "brain_gen": return await dbg_brain_gen(message)
    if cmd == "brain_show": return await dbg_brain_show(message)

    
    # G. Routing / Event
    if cmd == "route":       return await dbg_route(message, args)
    if cmd == "event":       return await dbg_event(message, args)
    if cmd == "chain":       return await dbg_chain(message, args)

    return _msg(f"Unknown debug command: {cmd}")
