"""Store immutable, nullable-dimension student portrait results by task/version."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0007_student_portrait_snapshot"
down_revision = "0006_student_analysis_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("""CREATE TABLE IF NOT EXISTS student_portrait_snapshot (
        snapshot_id VARCHAR(64) NOT NULL PRIMARY KEY,
        student_id VARCHAR(64) NOT NULL,
        source_task_id VARCHAR(64) NOT NULL,
        source_confirmation_task_id VARCHAR(64) NOT NULL,
        source_self_report_revision INT NOT NULL,
        target_job_id VARCHAR(64) DEFAULT NULL,
        status VARCHAR(20) NOT NULL,
        portrait_json JSON NOT NULL,
        content_sha256 CHAR(64) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uk_portrait_snapshot_task (source_task_id),
        KEY idx_portrait_snapshot_owner (student_id, created_at, snapshot_id),
        CONSTRAINT fk_portrait_snapshot_task FOREIGN KEY (source_task_id)
            REFERENCES pipeline_task(task_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""))
    inspector = inspect(connection)
    fields = {item["name"] for item in inspector.get_columns("student_portrait_snapshot")}
    required = {"snapshot_id", "student_id", "source_task_id", "source_confirmation_task_id",
                "source_self_report_revision", "target_job_id", "status", "portrait_json",
                "content_sha256", "created_at"}
    if missing := required - fields:
        raise RuntimeError(f"student_portrait_snapshot missing required columns: {sorted(missing)}")
    foreign_keys = inspector.get_foreign_keys("student_portrait_snapshot")
    if not any(item["name"] == "fk_portrait_snapshot_task"
               and item["referred_table"] == "pipeline_task"
               and item["constrained_columns"] == ["source_task_id"]
               and item.get("options", {}).get("ondelete", "").upper() == "CASCADE"
               for item in foreign_keys):
        raise RuntimeError("student_portrait_snapshot task foreign key is missing or incompatible")


def downgrade() -> None:
    raise RuntimeError("Versioned student portraits are not automatically downgraded")
