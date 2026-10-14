"""Add durable resume dispatch and owner-scoped multi-agent trace tables.

Both tables are additive. Existing rows are preserved; deployments that used
the earlier explicit installer are adopted without copying or truncating data.
"""

from alembic import op
from sqlalchemy import inspect, text


revision = "0004_resume_trace_schema"
down_revision = "0003_runtime_extension_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("""CREATE TABLE IF NOT EXISTS pipeline_resume_dispatch (
        task_id VARCHAR(64) NOT NULL PRIMARY KEY,
        claim_token CHAR(32) DEFAULT NULL,
        lease_until DATETIME(6) DEFAULT NULL,
        attempts INT NOT NULL DEFAULT 0,
        last_enqueued_at DATETIME(6) DEFAULT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_resume_replay (lease_until, last_enqueued_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""))
    connection.execute(text("""CREATE TABLE IF NOT EXISTS pipeline_task_trace (
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""))
    required = {
        "pipeline_resume_dispatch": {"task_id", "claim_token", "lease_until", "attempts", "last_enqueued_at"},
        "pipeline_task_trace": {"event_id", "task_id", "student_id", "stage", "state", "progress",
                                "source_name", "target_name", "facts_json", "occurred_at"},
    }
    for table, fields in required.items():
        present = {column["name"] for column in inspect(connection).get_columns(table)}
        if missing := fields - present:
            raise RuntimeError(f"{table} missing required columns: {sorted(missing)}")


def downgrade() -> None:
    raise RuntimeError("Resume task and trace history is not automatically downgraded")
