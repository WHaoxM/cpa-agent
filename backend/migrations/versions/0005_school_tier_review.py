"""Versioned, owner-bound official school-tier approvals and revocations."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0005_school_tier_review"
down_revision = "0004_resume_trace_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("""CREATE TABLE IF NOT EXISTS school_tier_review (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        student_id VARCHAR(64) NOT NULL,
        confirmation_task_id VARCHAR(64) NOT NULL,
        school_name VARCHAR(255) NOT NULL,
        school_tier VARCHAR(20) NOT NULL,
        source_kind VARCHAR(32) NOT NULL,
        source_uri VARCHAR(1000) NOT NULL,
        source_sha256 CHAR(64) NOT NULL,
        source_quote TEXT NOT NULL,
        quote_sha256 CHAR(64) NOT NULL,
        evidence_sha256 CHAR(64) NOT NULL,
        source_checked_at DATETIME(6) NOT NULL,
        status VARCHAR(20) NOT NULL,
        reviewed_by VARCHAR(64) NOT NULL,
        reviewed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        revoked_by VARCHAR(64) DEFAULT NULL,
        revoked_at DATETIME(6) DEFAULT NULL,
        revoke_reason VARCHAR(500) DEFAULT NULL,
        KEY idx_school_tier_review_current (student_id, confirmation_task_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""))
    required = {"id", "student_id", "confirmation_task_id", "school_name", "school_tier",
                "source_kind", "source_uri", "source_sha256", "source_quote", "quote_sha256",
                "evidence_sha256", "source_checked_at", "status", "reviewed_by", "reviewed_at",
                "revoked_by", "revoked_at", "revoke_reason"}
    present = {column["name"] for column in inspect(connection).get_columns("school_tier_review")}
    if missing := required - present:
        raise RuntimeError(f"school_tier_review missing required columns: {sorted(missing)}")


def downgrade() -> None:
    raise RuntimeError("School-tier approval history is not automatically downgraded")
