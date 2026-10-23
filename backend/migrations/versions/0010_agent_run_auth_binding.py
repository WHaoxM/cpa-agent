"""Bind queued Agent runs to the authenticated account epoch at submission."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0010_agent_run_auth_binding"
down_revision = "0009_agent_run_dispatch"
branch_labels = None
depends_on = None


_COLUMNS = {
    "principal_id": "VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL",
    "principal_source": "VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL",
    "principal_auth_epoch": "BIGINT UNSIGNED NULL",
}


def upgrade() -> None:
    connection = op.get_bind()
    existing = {item["name"] for item in inspect(connection).get_columns("agent_run")}
    for name, ddl in _COLUMNS.items():
        if name not in existing:
            connection.execute(text(f"ALTER TABLE agent_run ADD COLUMN {name} {ddl}"))
    columns = {item["name"] for item in inspect(connection).get_columns("agent_run")}
    missing = set(_COLUMNS) - columns
    if missing:
        raise RuntimeError(f"agent_run missing auth binding columns: {sorted(missing)}")


def downgrade() -> None:
    raise RuntimeError("Agent run account bindings must not be removed automatically")
