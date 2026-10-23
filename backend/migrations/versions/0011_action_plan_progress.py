"""Persist report-linked action-plan steps and owner progress."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0011_action_plan_progress"
down_revision = "0010_agent_run_auth_binding"
branch_labels = None
depends_on = None


_NEW_COLUMNS = {
    "handoff_id": "VARCHAR(64) NULL",
    "report_uri": "VARCHAR(255) NULL",
    "report_version": "VARCHAR(128) NULL",
    "resume_version": "VARCHAR(128) NULL",
    "gap_summary": "MEDIUMTEXT NULL",
}


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "agent_action_plan" not in tables:
        connection.execute(text("""
            CREATE TABLE agent_action_plan (
                plan_id VARCHAR(64) NOT NULL PRIMARY KEY,
                student_id VARCHAR(64) NOT NULL,
                session_id VARCHAR(64) NOT NULL,
                title VARCHAR(160) NOT NULL,
                body_text MEDIUMTEXT NOT NULL,
                body_sha256 CHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                handoff_id VARCHAR(64) NULL,
                report_uri VARCHAR(255) NULL,
                report_version VARCHAR(128) NULL,
                resume_version VARCHAR(128) NULL,
                gap_summary MEDIUMTEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_plan_owner_session (student_id, session_id, created_at),
                UNIQUE KEY uq_action_plan_owner_handoff (student_id, handoff_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))
    else:
        existing = {item["name"] for item in inspect(connection).get_columns("agent_action_plan")}
        for name, ddl in _NEW_COLUMNS.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE agent_action_plan ADD COLUMN {name} {ddl}"))
        indexes = {item["name"] for item in inspect(connection).get_indexes("agent_action_plan")}
        if "uq_action_plan_owner_handoff" not in indexes:
            connection.execute(text(
                "CREATE UNIQUE INDEX uq_action_plan_owner_handoff "
                "ON agent_action_plan (student_id, handoff_id)"
            ))

    if "agent_action_plan_step" not in set(inspect(connection).get_table_names()):
        connection.execute(text("""
            CREATE TABLE agent_action_plan_step (
                step_id VARCHAR(64) NOT NULL PRIMARY KEY,
                plan_id VARCHAR(64) NOT NULL,
                position SMALLINT UNSIGNED NOT NULL,
                title VARCHAR(160) NOT NULL,
                detail VARCHAR(600) NOT NULL,
                completed TINYINT(1) NOT NULL DEFAULT 0,
                completed_at DATETIME NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_action_plan_step_position (plan_id, position),
                INDEX idx_action_plan_step_plan (plan_id, position),
                CONSTRAINT fk_action_plan_step_plan FOREIGN KEY (plan_id)
                    REFERENCES agent_action_plan(plan_id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

    required = {item["name"] for item in inspect(connection).get_columns("agent_action_plan")}
    if set(_NEW_COLUMNS) - required:
        raise RuntimeError("agent_action_plan action-plan source columns were not created")


def downgrade() -> None:
    raise RuntimeError("Persisted action-plan progress must not be removed automatically")
