"""Add owner-bound Agent run, immutable plan versions, steps, and event journal."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0008_agent_run_state"
down_revision = "0007_student_portrait_snapshot"
branch_labels = None
depends_on = None


_TABLES = (
    """CREATE TABLE IF NOT EXISTS agent_run (
        run_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
        student_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        session_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        idempotency_key_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        request_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        input_snapshot_version SMALLINT UNSIGNED NOT NULL,
        input_snapshot_redaction_version SMALLINT UNSIGNED NOT NULL,
        input_snapshot_bytes INT UNSIGNED NOT NULL,
        input_snapshot_json JSON NOT NULL,
        input_snapshot_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        status VARCHAR(32) NOT NULL,
        current_plan_version INT UNSIGNED NOT NULL DEFAULT 0,
        last_event_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
        cancel_requested_at DATETIME(6) DEFAULT NULL,
        final_result_json JSON DEFAULT NULL,
        final_result_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
        finalized_at DATETIME(6) DEFAULT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        UNIQUE KEY uk_agent_run_owner_idempotency
            (student_id, session_id, idempotency_key_sha256),
        UNIQUE KEY uk_agent_run_owner_scope (run_id, student_id),
        KEY idx_agent_run_owner_created (student_id, created_at, run_id),
        KEY idx_agent_run_session (student_id, session_id, created_at),
        KEY idx_agent_run_status_updated (status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci""",
    """CREATE TABLE IF NOT EXISTS agent_run_plan (
        run_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        plan_version INT UNSIGNED NOT NULL,
        goal_summary VARCHAR(1000) NOT NULL,
        plan_json JSON NOT NULL,
        plan_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (run_id, plan_version),
        CONSTRAINT fk_agent_run_plan_run FOREIGN KEY (run_id)
            REFERENCES agent_run(run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci""",
    """CREATE TABLE IF NOT EXISTS agent_run_step (
        run_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        plan_version INT UNSIGNED NOT NULL,
        step_index SMALLINT UNSIGNED NOT NULL,
        role VARCHAR(32) NOT NULL,
        goal VARCHAR(400) NOT NULL,
        skill_ids_json JSON NOT NULL,
        status VARCHAR(32) NOT NULL,
        attempts INT UNSIGNED NOT NULL DEFAULT 0,
        result_json JSON DEFAULT NULL,
        result_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
        error_code VARCHAR(64) DEFAULT NULL,
        started_at DATETIME(6) DEFAULT NULL,
        finished_at DATETIME(6) DEFAULT NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        PRIMARY KEY (run_id, plan_version, step_index),
        KEY idx_agent_run_step_status (run_id, plan_version, status, step_index),
        CONSTRAINT fk_agent_run_step_plan FOREIGN KEY (run_id, plan_version)
            REFERENCES agent_run_plan(run_id, plan_version)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci""",
    """CREATE TABLE IF NOT EXISTS agent_run_event (
        run_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        student_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        event_seq BIGINT UNSIGNED NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        run_status VARCHAR(32) NOT NULL,
        payload_json JSON NOT NULL,
        occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (run_id, event_seq),
        KEY idx_agent_run_event_owner_cursor (student_id, run_id, event_seq),
        CONSTRAINT fk_agent_run_event_run FOREIGN KEY (run_id, student_id)
            REFERENCES agent_run(run_id, student_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci""",
)

_REQUIRED_COLUMNS = {
    "agent_run": {
        "run_id", "student_id", "session_id", "idempotency_key_sha256",
        "request_sha256", "input_snapshot_version", "input_snapshot_redaction_version",
        "input_snapshot_bytes", "input_snapshot_json", "input_snapshot_sha256",
        "status", "current_plan_version", "last_event_seq", "cancel_requested_at",
        "final_result_json", "final_result_sha256", "finalized_at", "created_at", "updated_at",
    },
    "agent_run_plan": {
        "run_id", "plan_version", "goal_summary", "plan_json", "plan_sha256", "created_at",
    },
    "agent_run_step": {
        "run_id", "plan_version", "step_index", "role", "goal", "skill_ids_json",
        "status", "attempts", "result_json", "result_sha256", "error_code",
        "started_at", "finished_at", "updated_at",
    },
    "agent_run_event": {
        "run_id", "student_id", "event_seq", "event_type", "run_status",
        "payload_json", "occurred_at",
    },
}


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _TABLES:
        connection.execute(text(statement))

    inspector = inspect(connection)
    for table, required in _REQUIRED_COLUMNS.items():
        present = {item["name"] for item in inspector.get_columns(table)}
        if missing := required - present:
            raise RuntimeError(f"{table} missing required columns: {sorted(missing)}")

    foreign_keys = {
        table: {item.get("name") for item in inspector.get_foreign_keys(table)}
        for table in ("agent_run_plan", "agent_run_step", "agent_run_event")
    }
    expected = {
        "agent_run_plan": "fk_agent_run_plan_run",
        "agent_run_step": "fk_agent_run_step_plan",
        "agent_run_event": "fk_agent_run_event_run",
    }
    for table, name in expected.items():
        if name not in foreign_keys[table]:
            raise RuntimeError(f"{table} missing required foreign key {name}")


def downgrade() -> None:
    raise RuntimeError("Agent run histories and idempotency records are not automatically downgraded")
