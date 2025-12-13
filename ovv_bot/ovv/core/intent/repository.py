def save_intent(intent: Intent) -> None
def update_intent_state(intent_id: str, state: str) -> None
def find_recent_by_context(context_key: str, limit: int = 20) -> list[Intent]