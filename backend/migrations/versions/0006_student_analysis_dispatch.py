"""Add owner-confirmed portrait and resume diagnostic dispatch intents.

This adopts tables made by an earlier explicit installer and preserves all
existing task/result rows. The frozen legacy init SQL is deliberately unchanged.
"""

from alembic import op
from sqlalchemy import inspect, text


revision = "0006_student_analysis_dispatch"
down_revision = "0005_school_tier_review"
branch_labels = None
depends_on = None


_TABLES = {
    "pipeline_portrait_dispatch": ("idx_portrait_replay", "fk_portrait_dispatch_task"),
    "resume_diagnostic_dispatch": ("idx_diagnostic_replay", "fk_diagnostic_dispatch_task"),
}


def upgrade() -> None:
    connection = op.get_bind()
    for table, (index, foreign_key) in _TABLES.items():
        connection.execute(text(f"""CREATE TABLE IF NOT EXISTS {table} (
            task_id VARCHAR(64) NOT NULL PRIMARY KEY,
            claim_token CHAR(32) DEFAULT NULL,
            lease_until DATETIME(6) DEFAULT NULL,
            attempts INT NOT NULL DEFAULT 0,
            last_enqueued_at DATETIME(6) DEFAULT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            KEY {index} (lease_until, last_enqueued_at),
            CONSTRAINT {foreign_key} FOREIGN KEY (task_id)
                REFERENCES pipeline_task(task_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""))
        inspector = inspect(connection)
        present = {column["name"] for column in inspector.get_columns(table)}
        required = {"task_id", "claim_token", "lease_until", "attempts",
                    "last_enqueued_at", "created_at"}
        if missing := required - present:
            raise RuntimeError(f"{table} missing required columns: {sorted(missing)}")
        keys = inspector.get_foreign_keys(table)
        if not any(key["name"] == foreign_key and key["referred_table"] == "pipeline_task"
                   and key["constrained_columns"] == ["task_id"]
                   and key.get("options", {}).get("ondelete", "").upper() == "CASCADE"
                   for key in keys):
            if any(key["name"] == foreign_key for key in keys):
                raise RuntimeError(f"{table} has an incompatible {foreign_key}")
            connection.execute(text(
                f"ALTER TABLE {table} ADD CONSTRAINT {foreign_key} "
                "FOREIGN KEY (task_id) REFERENCES pipeline_task(task_id) ON DELETE CASCADE"))


def downgrade() -> None:
    raise RuntimeError("Student analysis dispatch and task history are not automatically downgraded")
