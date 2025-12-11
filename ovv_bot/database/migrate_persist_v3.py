CREATE_TABLE_TASK_SESSION = """
CREATE TABLE IF NOT EXISTS task_session (
    task_id TEXT PRIMARY KEY,
    user_id TEXT,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_seconds INTEGER
);
"""

CREATE_TABLE_TASK_LOG = """
CREATE TABLE IF NOT EXISTS task_log (
    id SERIAL PRIMARY KEY,
    task_id TEXT,
    event_type TEXT,
    content TEXT,
    created_at TIMESTAMP
);
"""
