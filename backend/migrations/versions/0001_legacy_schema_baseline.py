"""Legacy schema baseline: frozen 2026-10-14 init_mysql.sql table definitions.

Existing tables are not rewritten. This creates missing tables, but cannot infer or
silently repair arbitrary differences in pre-existing table definitions.
"""

from hashlib import sha256
from pathlib import Path

from alembic import op
from sqlalchemy import inspect, text


revision = "0001_legacy_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[2] / "scripts" / "mysql" / "init_mysql.sql"
_SHA256 = "3717c248f3397d89cb0ff7855c143d2a19d1139565e65479629ed89b5b177518"


def _table_statements():
    # Checkout line endings differ between Windows and Linux; content must not.
    data = _SQL_FILE.read_bytes().replace(b"\r\n", b"\n")
    if sha256(data).hexdigest() != _SHA256:
        raise RuntimeError(
            "init_mysql.sql changed after the migration baseline was frozen; "
            "restore it and add a new Alembic revision instead"
        )
    lines = [line for line in data.decode("utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("--")]
    statements = []
    current = []
    for line in lines:
        current.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(current).rstrip().removesuffix(";").strip()
            current = []
            if statement.upper().startswith("CREATE TABLE IF NOT EXISTS "):
                statements.append(statement)
            elif not statement.upper().startswith(("CREATE DATABASE ", "USE ", "SET ", "SELECT ")):
                raise RuntimeError(f"Unsupported baseline statement: {statement[:80]}")
    if current or len(statements) < 40:
        raise RuntimeError("Incomplete or unexpectedly short baseline SQL")
    return statements


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _table_statements():
        connection.execute(text(statement))
    required = {"student_profile", "job_posting", "pipeline_task", "user_account",
                "knowledge_base", "job_dataset", "career_report"}
    missing = required - set(inspect(connection).get_table_names())
    if missing:
        raise RuntimeError(f"Baseline tables missing: {sorted(missing)}")


def downgrade() -> None:
    raise RuntimeError("Irreversible baseline: restore a database backup instead")
