"""Bring formerly separate runtime journals and projections under Alembic."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0003_runtime_extension_schema"
down_revision = "0002_account_and_source_schema"
branch_labels = None
depends_on = None

_TABLES = (
    """CREATE TABLE IF NOT EXISTS resume_entity_review (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        student_id VARCHAR(64) NOT NULL,
        source_confirmation_task_id VARCHAR(64) NOT NULL,
        source_sha256 CHAR(64) NOT NULL,
        request_id VARCHAR(128) NOT NULL,
        payload_sha256 CHAR(64) NOT NULL,
        revision INT NOT NULL,
        reviews_json JSON NOT NULL,
        snapshot_json JSON NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uk_entity_review_request (student_id, request_id),
        UNIQUE KEY uk_entity_review_revision (student_id, source_confirmation_task_id, revision),
        KEY idx_entity_review_source (student_id, source_confirmation_task_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS personal_entity_link_projection (
        student_id VARCHAR(64) NOT NULL,
        source_task_id VARCHAR(64) NOT NULL,
        review_revision INT NOT NULL,
        status VARCHAR(20) NOT NULL,
        payload_json JSON NOT NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (student_id, source_task_id, review_revision),
        KEY idx_entity_link_retry (status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS personal_graph_projection (
        student_id VARCHAR(64) NOT NULL,
        source_task_id VARCHAR(64) NOT NULL,
        status VARCHAR(20) NOT NULL,
        payload_json JSON NOT NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (student_id, source_task_id),
        KEY idx_graph_projection_status (status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci""",
    """CREATE TABLE IF NOT EXISTS agent_fork_usage (
        job_id VARCHAR(64) NOT NULL PRIMARY KEY,
        session_id VARCHAR(128) NOT NULL,
        student_id VARCHAR(64) NOT NULL,
        usage_turn_id VARCHAR(64) NOT NULL,
        kind VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        ledger_json TEXT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS pipeline_task_event (
        event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        task_id VARCHAR(64) NOT NULL,
        student_id VARCHAR(64) NULL,
        status VARCHAR(20) NOT NULL,
        occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_task_cursor (task_id, event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci""",
)

_TRIGGERS = {
    "cpa_pipeline_created_v1": """CREATE TRIGGER cpa_pipeline_created_v1
        AFTER INSERT ON pipeline_task FOR EACH ROW
        INSERT INTO pipeline_task_event(task_id, student_id, status)
        VALUES(NEW.task_id, NEW.student_id, NEW.status)""",
    "cpa_pipeline_transition_v1": """CREATE TRIGGER cpa_pipeline_transition_v1
        AFTER UPDATE ON pipeline_task FOR EACH ROW
        BEGIN
          IF NOT (OLD.status <=> NEW.status) THEN
            INSERT INTO pipeline_task_event(task_id, student_id, status)
            VALUES(NEW.task_id, NEW.student_id, NEW.status);
          END IF;
        END""",
}


def upgrade() -> None:
    connection = op.get_bind()
    for ddl in _TABLES:
        connection.execute(text(ddl))
    columns = {item["name"] for item in inspect(connection).get_columns("chapter")}
    if "video_url" not in columns:
        connection.execute(text("ALTER TABLE chapter ADD COLUMN video_url VARCHAR(500) DEFAULT NULL"))
    if "video_provider" not in columns:
        connection.execute(text("ALTER TABLE chapter ADD COLUMN video_provider VARCHAR(64) DEFAULT NULL"))
    triggers = {row[0] for row in connection.execute(text(
        "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
        "WHERE TRIGGER_SCHEMA = DATABASE()"))}
    for name, ddl in _TRIGGERS.items():
        if name not in triggers:
            connection.execute(text(ddl))
    required = {"resume_entity_review", "personal_entity_link_projection",
                "personal_graph_projection", "agent_fork_usage", "pipeline_task_event"}
    missing = required - set(inspect(connection).get_table_names())
    if missing:
        raise RuntimeError(f"Runtime extension tables missing: {sorted(missing)}")


def downgrade() -> None:
    raise RuntimeError("Runtime journal/projection history is not automatically downgraded")
