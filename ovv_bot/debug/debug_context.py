# debug/debug_context.py

class DebugContext:
    pg_conn = None
    notion = None
    openai_client = None

    load_mem = None
    save_mem = None
    append_mem = None

    brain_gen = None
    brain_load = None
    brain_save = None

    ovv_core = None
    ovv_external = None
    system_prompt = None

debug_context = DebugContext()
