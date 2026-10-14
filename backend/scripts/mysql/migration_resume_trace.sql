-- Historical compatibility installer for the L0 tables, released before the
-- unified Alembic chain. New/updated deployments use `alembic upgrade head`
-- and revision 0004_resume_trace_schema instead. Additive and idempotent.

CREATE TABLE IF NOT EXISTS pipeline_resume_dispatch (
    task_id VARCHAR(64) NOT NULL PRIMARY KEY,
    claim_token CHAR(32) DEFAULT NULL,
    lease_until DATETIME(6) DEFAULT NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_enqueued_at DATETIME(6) DEFAULT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_resume_replay (lease_until, last_enqueued_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS pipeline_task_trace (
    event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    student_id VARCHAR(64) NOT NULL,
    stage VARCHAR(40) NOT NULL,
    state VARCHAR(20) NOT NULL,
    progress TINYINT UNSIGNED NOT NULL,
    source_name VARCHAR(60) NOT NULL,
    target_name VARCHAR(60) NOT NULL,
    facts_json JSON DEFAULT NULL,
    occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_resume_trace_cursor (task_id, event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
