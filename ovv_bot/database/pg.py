def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40) -> None:
    """Append message to runtime_memory (bounded length)."""
    mem = load_runtime_memory(session_id)
    mem.append(
        {
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    if len(mem) > limit:
        mem = mem[-limit:]
    save_runtime_memory(session_id, mem)


# ============================================================
# THREAD BRAIN: I/O
# ============================================================
def generate_thread_brain(