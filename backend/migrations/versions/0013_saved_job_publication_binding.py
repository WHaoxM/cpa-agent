"""Bind catalog favorites to the exact immutable job publication."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0013_saved_job_pub_binding"
down_revision = "0012_action_plan_step_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(inspect(connection).get_table_names())
    if "saved_job" not in tables or "job_catalog_publication" not in tables:
        raise RuntimeError("saved_job and job_catalog_publication must exist before binding favorites")

    columns = {item["name"] for item in inspect(connection).get_columns("saved_job")}
    if "publication_id" not in columns:
        connection.execute(text("ALTER TABLE saved_job ADD COLUMN publication_id BIGINT NULL"))

    indexes = {item["name"] for item in inspect(connection).get_indexes("saved_job")}
    if "uk_saved_job_owner_publication" not in indexes:
        connection.execute(text(
            "CREATE UNIQUE INDEX uk_saved_job_owner_publication "
            "ON saved_job (user_id, publication_id)"
        ))

    foreign_keys = {item["name"] for item in inspect(connection).get_foreign_keys("saved_job")}
    if "fk_saved_job_publication" not in foreign_keys:
        connection.execute(text(
            "ALTER TABLE saved_job ADD CONSTRAINT fk_saved_job_publication "
            "FOREIGN KEY (publication_id) REFERENCES job_catalog_publication(id)"
        ))

    required = {item["name"] for item in inspect(connection).get_columns("saved_job")}
    if "publication_id" not in required:
        raise RuntimeError("saved_job publication binding was not created")


def downgrade() -> None:
    raise RuntimeError("Saved publication-version history must not be removed automatically")
