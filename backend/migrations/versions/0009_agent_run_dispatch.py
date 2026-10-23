"""Add the durable Agent Celery outbox, publisher lease, and worker claim."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0009_agent_run_dispatch"
down_revision = "0008_agent_run_state"
branch_labels = None
depends_on = None


_DDL = """CREATE TABLE IF NOT EXISTS agent_run_dispatch (
    run_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    dispatch_state VARCHAR(32) NOT NULL,
    publish_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    publish_lease_until DATETIME(6) DEFAULT NULL,
    publish_attempts INT UNSIGNED NOT NULL DEFAULT 0,
    next_publish_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_enqueued_at DATETIME(6) DEFAULT NULL,
    claim_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    lease_until DATETIME(6) DEFAULT NULL,
    claim_attempts INT UNSIGNED NOT NULL DEFAULT 0,
    last_error_code VARCHAR(64) DEFAULT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    KEY idx_agent_run_dispatch_publish (dispatch_state, next_publish_at, last_enqueued_at),
    KEY idx_agent_run_dispatch_lease (dispatch_state, lease_until),
    CONSTRAINT fk_agent_run_dispatch_run FOREIGN KEY (run_id)
        REFERENCES agent_run(run_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""

_REQUIRED_COLUMNS = {
    "run_id", "dispatch_state", "publish_token", "publish_lease_until",
    "publish_attempts", "next_publish_at", "last_enqueued_at", "claim_token",
    "lease_until", "claim_attempts", "last_error_code", "created_at", "updated_at",
}


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(text(_DDL))
    columns = {item["name"] for item in inspect(connection).get_columns("agent_run_dispatch")}
    if missing := _REQUIRED_COLUMNS - columns:
        raise RuntimeError(f"agent_run_dispatch missing required columns: {sorted(missing)}")
    foreign_keys = {item.get("name") for item in inspect(connection).get_foreign_keys(
        "agent_run_dispatch"
    )}
    if "fk_agent_run_dispatch_run" not in foreign_keys:
        raise RuntimeError("agent_run_dispatch missing required run foreign key")

    # Rows accepted by the persistence-only API before this revision were not
    # sent to a broker. Keep them explicitly waiting; never surprise-dispatch
    # old user input merely because an operator applied the schema upgrade.
    connection.execute(text("""
    INSERT IGNORE INTO agent_run_dispatch (run_id, dispatch_state)
    SELECT run_id,
        CASE
            WHEN status IN ('completed','failed') THEN 'terminal'
            WHEN status = 'cancelled' THEN 'cancelled'
            WHEN status = 'needs_reconciliation' THEN 'needs_reconciliation'
            WHEN status = 'queued' THEN 'not_dispatched'
            ELSE 'needs_reconciliation'
        END
        FROM agent_run
    """))


def downgrade() -> None:
    raise RuntimeError("Agent dispatch history is not automatically downgraded")
