"""Persist revisioned task contracts and exact output confirmations."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0021_agent_task_contract"
down_revision = "0020_career_pathway"
branch_labels = None
depends_on = None


_RUN_COLUMNS = {
    "current_contract_revision": "INT UNSIGNED NOT NULL DEFAULT 0",
    "pause_requested_at": "DATETIME(6) DEFAULT NULL",
}

_CONTRACT_DDL = """CREATE TABLE IF NOT EXISTS agent_run_task_contract_revision (
    run_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    student_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    revision INT UNSIGNED NOT NULL,
    contract_json JSON NOT NULL,
    contract_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    idempotency_key_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin DEFAULT NULL,
    request_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    mutation_kind VARCHAR(32) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (run_id, revision),
    UNIQUE KEY uq_agent_run_contract_mutation (run_id, idempotency_key_sha256),
    KEY idx_agent_run_contract_owner (student_id, run_id, revision),
    CONSTRAINT fk_agent_run_contract_owner FOREIGN KEY (run_id, student_id)
        REFERENCES agent_run(run_id, student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""

_CONFIRMATION_DDL = """CREATE TABLE IF NOT EXISTS agent_run_task_write_confirmation (
    run_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    student_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    contract_revision INT UNSIGNED NOT NULL,
    plan_version INT UNSIGNED NOT NULL,
    plan_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    action VARCHAR(32) NOT NULL,
    target VARCHAR(256) NOT NULL,
    target_version VARCHAR(128) NOT NULL,
    confirmation_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    idempotency_key_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    artifact_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    artifact_saved_at DATETIME(6) DEFAULT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (run_id, contract_revision, plan_version, action),
    UNIQUE KEY uq_agent_run_confirmation_key (run_id, idempotency_key_sha256),
    UNIQUE KEY uq_agent_run_confirmation_id (confirmation_id),
    KEY idx_agent_run_confirmation_owner (student_id, run_id, created_at),
    CONSTRAINT fk_agent_run_confirmation_contract FOREIGN KEY (run_id, contract_revision)
        REFERENCES agent_run_task_contract_revision(run_id, revision),
    CONSTRAINT fk_agent_run_confirmation_owner FOREIGN KEY (run_id, student_id)
        REFERENCES agent_run(run_id, student_id),
    CONSTRAINT fk_agent_run_confirmation_plan FOREIGN KEY (run_id, plan_version)
        REFERENCES agent_run_plan(run_id, plan_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""

_FOLLOWUP_DDL = """CREATE TABLE IF NOT EXISTS agent_run_task_followup (
    run_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    student_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    idempotency_key_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    contract_revision INT UNSIGNED NOT NULL,
    request_json JSON NOT NULL,
    operation_status VARCHAR(32) NOT NULL,
    model_required TINYINT(1) NOT NULL DEFAULT 1,
    reserved_model_calls INT UNSIGNED NOT NULL DEFAULT 0,
    auto_paused TINYINT(1) NOT NULL DEFAULT 0,
    active_slot TINYINT NULL DEFAULT 1,
    classification_json JSON DEFAULT NULL,
    model_usage_json JSON DEFAULT NULL,
    result_json JSON DEFAULT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (run_id, idempotency_key_sha256),
    UNIQUE KEY uq_agent_run_followup_active (run_id, active_slot),
    KEY idx_agent_run_followup_owner (student_id, run_id, created_at),
    CONSTRAINT fk_agent_run_followup_contract FOREIGN KEY (run_id, contract_revision)
        REFERENCES agent_run_task_contract_revision(run_id, revision),
    CONSTRAINT fk_agent_run_followup_owner FOREIGN KEY (run_id, student_id)
        REFERENCES agent_run(run_id, student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    present_columns = {item["name"] for item in inspector.get_columns("agent_run")}
    for name, definition in _RUN_COLUMNS.items():
        if name not in present_columns:
            connection.execute(text(f"ALTER TABLE agent_run ADD COLUMN {name} {definition}"))

    connection.execute(text(_CONTRACT_DDL))
    connection.execute(text(_CONFIRMATION_DDL))
    connection.execute(text(_FOLLOWUP_DDL))

    inspector = inspect(connection)
    required_contract = {
        "run_id", "student_id", "revision", "contract_json", "contract_sha256",
        "idempotency_key_sha256", "request_sha256", "mutation_kind", "created_at",
    }
    required_confirmation = {
        "run_id", "student_id", "contract_revision", "plan_version", "plan_sha256",
        "action", "target", "target_version", "confirmation_id",
        "idempotency_key_sha256", "request_sha256", "artifact_id",
        "artifact_saved_at", "created_at",
    }
    required_followup = {
        "run_id", "student_id", "idempotency_key_sha256", "request_sha256",
        "contract_revision", "request_json", "operation_status", "model_required",
        "reserved_model_calls", "auto_paused", "active_slot", "classification_json",
        "model_usage_json", "result_json", "created_at", "updated_at",
    }
    for table, required in (
        ("agent_run_task_contract_revision", required_contract),
        ("agent_run_task_write_confirmation", required_confirmation),
        ("agent_run_task_followup", required_followup),
    ):
        columns = {item["name"] for item in inspector.get_columns(table)}
        if missing := required - columns:
            raise RuntimeError(f"{table} missing required columns: {sorted(missing)}")

    foreign_keys = {
        table: {item.get("name") for item in inspector.get_foreign_keys(table)}
        for table in (
            "agent_run_task_contract_revision", "agent_run_task_write_confirmation",
            "agent_run_task_followup",
        )
    }
    expected = {
        "agent_run_task_contract_revision": {"fk_agent_run_contract_owner"},
        "agent_run_task_write_confirmation": {
            "fk_agent_run_confirmation_contract", "fk_agent_run_confirmation_owner",
            "fk_agent_run_confirmation_plan",
        },
        "agent_run_task_followup": {
            "fk_agent_run_followup_contract", "fk_agent_run_followup_owner",
        },
    }
    for table, names in expected.items():
        if missing := names - foreign_keys[table]:
            raise RuntimeError(f"{table} missing required foreign keys: {sorted(missing)}")


def downgrade() -> None:
    raise RuntimeError("Task contract and write-confirmation history is not automatically downgraded")
