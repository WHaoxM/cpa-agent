"""MySQL schema migration contract; live round-trip is explicitly opt-in."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

from alembic.script import ScriptDirectory

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


def test_migration_revision_ids_fit_alembic_default_version_column():
    scripts = ScriptDirectory(str(BACKEND / "migrations"))
    revisions = [revision.revision for revision in scripts.walk_revisions()]
    assert revisions
    assert all(len(revision) <= 32 for revision in revisions), revisions


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_MYSQL=1 for disposable real-MySQL migration test")
@pytest.mark.parametrize("preinstalled_dispatch", [False, True])
def test_clean_and_legacy_upgrade_on_disposable_mysql_database(preinstalled_dispatch):
    import pymysql
    from app.config import Config
    from production_wsgi import require_schema_head

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
        probe_config = SimpleNamespace(
            MYSQL_HOST=Config.MYSQL_HOST, MYSQL_PORT=Config.MYSQL_PORT,
            MYSQL_USER=Config.MYSQL_USER, MYSQL_PASSWORD=Config.MYSQL_PASSWORD,
            MYSQL_DATABASE=name,
        )
        with pytest.raises(RuntimeError, match="Alembic head"):
            require_schema_head(probe_config)
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
                if preinstalled_dispatch:
                    # Earlier explicit installers created these tables outside
                    # Alembic. Adopt both, preserving rows and adding FK fences.
                    cursor.execute("INSERT INTO pipeline_task(task_id,student_id,pipeline,status) "
                                   "VALUES ('task_migration_probe','migration_probe',"
                                   "'L2_generate_portrait','pending')")
                    for table in ("pipeline_portrait_dispatch", "resume_diagnostic_dispatch"):
                        cursor.execute(f"CREATE TABLE {table} ("
                                       "task_id VARCHAR(64) NOT NULL PRIMARY KEY,"
                                       "claim_token CHAR(32) DEFAULT NULL,"
                                       "lease_until DATETIME(6) DEFAULT NULL,"
                                       "attempts INT NOT NULL DEFAULT 0,"
                                       "last_enqueued_at DATETIME(6) DEFAULT NULL,"
                                       "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
                                       "KEY idx_replay (lease_until,last_enqueued_at)"
                                       ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
                        cursor.execute(f"INSERT INTO {table}(task_id) "
                                       "VALUES ('task_migration_probe')")
                    from app.services.student_portrait_snapshot import SNAPSHOT_DDL
                    cursor.execute(SNAPSHOT_DDL)
                    cursor.execute("INSERT INTO student_portrait_snapshot "
                                   "(snapshot_id,student_id,source_task_id,"
                                   "source_confirmation_task_id,source_self_report_revision,"
                                   "status,portrait_json,content_sha256) VALUES "
                                   "('ps_migration_probe','migration_probe','task_migration_probe',"
                                   "'task_resume_probe',0,'partial','{}',REPEAT('0',64))")
            alembic("upgrade", "0008_agent_run_state")
            with database.cursor() as cursor:
                for number in (1, 2):
                    cursor.execute("INSERT INTO student_self_assessment "
                                   "(assessment_id,student_id,assessed_skills,assessed_abilities,weak_skills,recommended_roles) "
                                   "VALUES (%s,'migration_probe',JSON_ARRAY(),JSON_OBJECT('sql',25),JSON_ARRAY('SQL'),JSON_ARRAY())",
                                   (f'legacy_reflection_{number}',))
                cursor.execute("""
                    INSERT INTO agent_run
                    (run_id,student_id,session_id,idempotency_key_sha256,request_sha256,
                     input_snapshot_version,input_snapshot_redaction_version,input_snapshot_bytes,
                     input_snapshot_json,input_snapshot_sha256,status,current_plan_version,
                     last_event_seq,created_at,updated_at)
                    VALUES ('run_migration_probe','migration_probe','session_probe',
                            REPEAT('1',64),REPEAT('2',64),1,1,2,
                            JSON_OBJECT('schema_version',1,'redaction_version',1,'message','x'),
                            REPEAT('2',64),'queued',0,0,NOW(6),NOW(6))
                """)
            alembic("upgrade", "head")
            alembic("upgrade", "head")
            require_schema_head(probe_config)
            with database.cursor() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version")
                assert cursor.fetchone() == (ScriptDirectory(str(BACKEND / 'migrations')).get_current_head(),)
                cursor.execute("SELECT COLUMN_NAME,IS_NULLABLE FROM information_schema.COLUMNS "
                               "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='student_self_assessment' "
                               "AND COLUMN_NAME IN ('reflection_snapshot','submission_id')")
                assert set(cursor.fetchall()) == {('reflection_snapshot','YES'),('submission_id','YES')}
                cursor.execute("SHOW INDEX FROM student_self_assessment WHERE Key_name='uq_reflection_owner_submission'")
                index_rows=cursor.fetchall()
                assert len(index_rows)==2 and all(row[1]==0 for row in index_rows)
                cursor.execute("SELECT assessment_id,assessed_abilities,reflection_snapshot,submission_id "
                               "FROM student_self_assessment WHERE student_id='migration_probe' ORDER BY assessment_id")
                legacy_rows=cursor.fetchall()
                assert len(legacy_rows)==2
                assert all(json.loads(row[1])=={'sql':25} and row[2:] == (None,None) for row in legacy_rows)
                cursor.execute("SHOW COLUMNS FROM student_resume_draft")
                columns={row[0]:row for row in cursor.fetchall()}
                assert set(columns) == {'student_id','revision','form_json','theme','updated_at','guidance_json'}
                assert columns['guidance_json'][2]=='YES'
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
                cursor.execute("SHOW TABLES LIKE 'school_tier_review'")
                assert cursor.fetchone() is not None
                for table in ("pipeline_portrait_dispatch", "resume_diagnostic_dispatch"):
                    cursor.execute(f"SHOW TABLES LIKE '{table}'")
                    assert cursor.fetchone() is not None
                    if preinstalled_dispatch:
                        cursor.execute(f"SELECT attempts FROM {table} "
                                       "WHERE task_id='task_migration_probe'")
                        assert cursor.fetchone() == (0,)
                    cursor.execute("SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
                                   "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                                   "AND REFERENCED_TABLE_NAME = 'pipeline_task'", (table,))
                    assert cursor.fetchone() == ("CASCADE",)
                cursor.execute("SHOW TABLES LIKE 'student_portrait_snapshot'")
                assert cursor.fetchone() is not None
                for table in ("agent_run", "agent_run_plan", "agent_run_step", "agent_run_event",
                              "agent_run_dispatch", "agent_action_plan", "agent_action_plan_step",
                              "agent_action_plan_step_evidence", "career_exploration", "career_exploration_round"):
                    cursor.execute(f"SHOW TABLES LIKE '{table}'")
                    assert cursor.fetchone() is not None
                cursor.execute("SHOW COLUMNS FROM career_exploration_round LIKE 'closed_reason'")
                assert cursor.fetchone() is not None
                cursor.execute("SHOW COLUMNS FROM agent_action_plan LIKE 'exploration_round_id'")
                assert cursor.fetchone() is not None
                cursor.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                               "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='saved_job' "
                               "AND COLUMN_NAME='publication_id'")
                assert cursor.fetchone() == ("publication_id",)
                cursor.execute("SELECT CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS "
                               "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='saved_job' "
                               "AND CONSTRAINT_NAME='fk_saved_job_publication'")
                assert cursor.fetchone() == ("fk_saved_job_publication",)
                cursor.execute("SELECT dispatch_state FROM agent_run_dispatch "
                               "WHERE run_id='run_migration_probe'")
                assert cursor.fetchone() == ("not_dispatched",)
                cursor.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                               "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='agent_run' "
                               "AND COLUMN_NAME IN ('input_snapshot_json','input_snapshot_version',"
                               "'input_snapshot_redaction_version','input_snapshot_bytes',"
                               "'final_result_json','final_result_sha256','finalized_at',"
                               "'principal_id','principal_source','principal_auth_epoch')")
                assert {row[0] for row in cursor.fetchall()} == {
                    "input_snapshot_json", "input_snapshot_version",
                    "input_snapshot_redaction_version", "input_snapshot_bytes",
                    "final_result_json", "final_result_sha256", "finalized_at",
                    "principal_id", "principal_source", "principal_auth_epoch",
                }
                if preinstalled_dispatch:
                    cursor.execute("SELECT source_task_id FROM student_portrait_snapshot "
                                   "WHERE snapshot_id='ps_migration_probe'")
                    assert cursor.fetchone() == ("task_migration_probe",)
                cursor.execute("SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
                               "WHERE CONSTRAINT_SCHEMA = DATABASE() "
                               "AND TABLE_NAME = 'student_portrait_snapshot' "
                               "AND REFERENCED_TABLE_NAME = 'pipeline_task'")
                assert cursor.fetchone() == ("CASCADE",)
        finally:
            database.close()
    finally:
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()
