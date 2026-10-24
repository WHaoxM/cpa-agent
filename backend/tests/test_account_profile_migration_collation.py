from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0022_account_profile_preferences.py"
)
SPEC = importlib.util.spec_from_file_location("account_profile_migration", MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.row = row

    def execute(self, statement):
        assert "INFORMATION_SCHEMA.COLUMNS" in str(statement)
        return _Result(self.row)


def test_profile_foreign_key_column_inherits_account_id_collation():
    ddl = MIGRATION._preferences_ddl(_Connection({
        "COLUMN_TYPE": "varchar(64)",
        "IS_NULLABLE": "NO",
        "CHARACTER_SET_NAME": "utf8mb4",
        "COLLATION_NAME": "utf8mb4_0900_ai_ci",
    }))

    assert "user_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL" in ddl
    assert "FOREIGN KEY (user_id) REFERENCES user_account(id)" in ddl


@pytest.mark.parametrize("changes", [
    {"COLUMN_TYPE": "varchar(128)"},
    {"IS_NULLABLE": "YES"},
    {"COLLATION_NAME": "utf8mb4_0900_ai_ci; DROP TABLE user_account"},
])
def test_profile_migration_rejects_incompatible_or_unsafe_parent_identity(changes):
    row = {
        "COLUMN_TYPE": "varchar(64)",
        "IS_NULLABLE": "NO",
        "CHARACTER_SET_NAME": "utf8mb4",
        "COLLATION_NAME": "utf8mb4_unicode_ci",
    }
    row.update(changes)

    with pytest.raises(RuntimeError):
        MIGRATION._preferences_ddl(_Connection(row))
