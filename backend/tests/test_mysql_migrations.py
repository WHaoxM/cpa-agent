"""MySQL schema migration contract; live round-trip is explicitly opt-in."""

import importlib
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


BACKEND = Path(__file__).resolve().parents[1]
BASELINE = importlib.import_module("migrations.versions.0001_legacy_schema_baseline")


def test_baseline_sql_is_frozen_and_table_only():
    statements = BASELINE._table_statements()
    assert len(statements) >= 40
    assert all(statement.startswith("CREATE TABLE IF NOT EXISTS ") for statement in statements)


def test_baseline_rejects_editing_old_init_sql(tmp_path, monkeypatch):
    edited = tmp_path / "init_mysql.sql"
    edited.write_text("CREATE TABLE changed (id INT);", encoding="utf-8")
    monkeypatch.setattr(BASELINE, "_SQL_FILE", edited)
    with pytest.raises(RuntimeError, match="add a new Alembic revision"):
        BASELINE._table_statements()


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_MYSQL=1 for disposable real-MySQL migration test")
def test_clean_and_legacy_upgrade_on_disposable_mysql_database():
    import pymysql
    from app.config import Config

    name = "cpa_migration_test_" + uuid.uuid4().hex[:12]
    admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            autocommit=True)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={name}", *args],
            cwd=BACKEND, check=True, capture_output=True, text=True,
        )

    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        alembic("upgrade", "0001_legacy_schema_baseline")
        database = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                                   user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                                   database=name, autocommit=True)
        try:
            with database.cursor() as cursor:
                # Emulate an older volume while retaining a real business row.
                cursor.execute("INSERT INTO user_account (id, username, password_hash) "
                               "VALUES ('migration_probe', 'migration_probe', 'hash')")
                cursor.execute("ALTER TABLE user_account DROP COLUMN auth_epoch")
                cursor.execute("ALTER TABLE knowledge_base DROP COLUMN content_sha256")
                cursor.execute("ALTER TABLE job_dataset DROP COLUMN staged_record_count, "
                               "DROP COLUMN content_sha256")
                cursor.execute("DROP TABLE job_catalog_source_listing")
            alembic("upgrade", "head")
            alembic("upgrade", "head")
            with database.cursor() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version")
                assert cursor.fetchone() == ("0004_resume_trace_schema",)
                cursor.execute("SELECT username, auth_epoch FROM user_account "
                               "WHERE id='migration_probe'")
                assert cursor.fetchone() == ("migration_probe", 0)
                cursor.execute("UPDATE user_account SET status='disabled' "
                               "WHERE id='migration_probe'")
                cursor.execute("UPDATE user_account SET name='unchanged_epoch' "
                               "WHERE id='migration_probe'")
                cursor.execute("SELECT auth_epoch FROM user_account "
                               "WHERE id='migration_probe'")
                assert cursor.fetchone() == (1,)
                cursor.execute("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                               "WHERE TRIGGER_SCHEMA = DATABASE() "
                               "AND TRIGGER_NAME='cpa_user_account_auth_epoch_v1'")
                assert cursor.fetchone() == ("cpa_user_account_auth_epoch_v1",)
                cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS "
                               "WHERE TABLE_SCHEMA = DATABASE() AND "
                               "((TABLE_NAME='knowledge_base' AND COLUMN_NAME='content_sha256') "
                               "OR (TABLE_NAME='job_dataset' AND COLUMN_NAME IN "
                               "('content_sha256','staged_record_count')))")
                assert cursor.fetchone() == (3,)
                cursor.execute("SHOW TABLES LIKE 'job_catalog_source_listing'")
                assert cursor.fetchone() is not None
                cursor.execute("SHOW TABLES LIKE 'pipeline_task_event'")
                assert cursor.fetchone() is not None
                cursor.execute("SHOW TABLES LIKE 'pipeline_resume_dispatch'")
                assert cursor.fetchone() is not None
                cursor.execute("SHOW TABLES LIKE 'pipeline_task_trace'")
                assert cursor.fetchone() is not None
        finally:
            database.close()
    finally:
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()
