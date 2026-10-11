-- Apply explicitly before model-backed fork summary work is dispatched.
CREATE TABLE IF NOT EXISTS agent_fork_usage (
    job_id VARCHAR(64) NOT NULL PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    student_id VARCHAR(64) NOT NULL,
    usage_turn_id VARCHAR(64) NOT NULL,
    kind VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    ledger_json TEXT NOT NULL
);
