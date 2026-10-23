"""Persist student-submitted evidence for individual action-plan steps."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0012_action_plan_step_evidence"
down_revision = "0011_action_plan_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if "agent_action_plan_step_evidence" in set(inspect(connection).get_table_names()):
        return
    connection.execute(text("""
        CREATE TABLE agent_action_plan_step_evidence (
            step_id VARCHAR(64) NOT NULL PRIMARY KEY,
            plan_id VARCHAR(64) NOT NULL,
            student_id VARCHAR(64) NOT NULL,
            summary MEDIUMTEXT NOT NULL,
            attachment_file_id VARCHAR(64) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_action_plan_evidence_owner_plan (student_id, plan_id),
            CONSTRAINT fk_action_plan_evidence_plan FOREIGN KEY (plan_id)
                REFERENCES agent_action_plan(plan_id) ON DELETE CASCADE,
            CONSTRAINT fk_action_plan_evidence_step FOREIGN KEY (step_id)
                REFERENCES agent_action_plan_step(step_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))
    required = {item["name"] for item in inspect(connection).get_columns("agent_action_plan_step_evidence")}
    if not {"step_id", "plan_id", "student_id", "summary", "attachment_file_id"} <= required:
        raise RuntimeError("agent_action_plan_step_evidence schema was not created")


def downgrade() -> None:
    raise RuntimeError("Student-submitted action-plan evidence must not be removed automatically")
