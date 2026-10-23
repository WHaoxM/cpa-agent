"""Exact school facts remain sourced, owner-bound, read-only and non-scoring."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services.agent_runtime.school_facts import (
    lookup_historical_211,
    project_verified_school_facts,
    read_owner_school_context,
)


class ConfirmedProfileRepo:
    def __init__(self, school="北京大学"):
        self.owner = "school_owner_001"
        self.task_id = "resume_confirmed_001"
        self.candidate = {"school": school, "degree": "本科", "major": "计算机科学与技术"}
        self.profile = {**self.candidate, "_resume_confirmation": {
            "task_id": self.task_id, "student_id": self.owner,
        }}
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, params))
        if "FROM student_profile" in sql:
            return ([{"profile_json": json.dumps(self.profile, ensure_ascii=False)}]
                    if params.get("sid") == self.owner else [])
        if "FROM pipeline_task" in sql and params.get("task_id") == self.task_id:
            return [{"task_id": self.task_id, "student_id": self.owner,
                     "pipeline": "L0_parse_resume", "status": "completed",
                     "result_json": json.dumps({"confirmed": True,
                                                "candidate_profile": self.candidate}, ensure_ascii=False)}]
        return []

def test_official_historical_211_exact_match_has_provenance():
    fact = lookup_historical_211("北京大学")
    assert fact["state"] == "yes"
    assert fact["match_basis"] == "exact"
    assert fact["matched_name"] == "北京大学"
    assert fact["source_id"] == "moe-211-schools-2005"
    assert fact["source_url"].startswith("https://www.moe.gov.cn/")
    assert len(fact["source_html_sha256"]) == 64
    assert len(fact["source_data_sha256"]) == 64
    assert fact["scoring_effect"] == "none"


def test_unlisted_and_ambiguous_names_remain_unknown_not_negative():
    for name in ("某普通本科", "北京大学某校区", "国防科技大学", ""):
        fact = lookup_historical_211(name)
        assert fact["state"] == "unknown"
        assert fact["matched_name"] is None
        assert fact["match_basis"] == "unknown"
        assert fact["scoring_effect"] == "none"


def test_confirmed_owner_school_context_carries_both_versions_without_scoring_write():
    repo = ConfirmedProfileRepo()
    result = read_owner_school_context(repo, student_id=repo.owner)
    assert result["status"] == "matched_official_name"
    assert result["profile_assurance"] == "user_confirmed_resume_not_enrollment_verified"
    assert result["profile_version"] == repo.task_id
    assert result["knowledge_version"] == "moe-211-schools-2005"
    assert result["institution_facts"][0]["state"] == "yes"
    assert result["institution_facts"][0]["source_data_sha256"]
    assert result["scoring_effect"] == "none"
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in repo.calls)


def test_projected_facts_include_only_source_backed_fields_and_no_document_number():
    repo = ConfirmedProfileRepo()
    context = read_owner_school_context(repo, student_id=repo.owner)
    facts = project_verified_school_facts(context)

    assert len(facts) == 1
    fact = facts[0]
    assert fact["kind"] == "historical_school_label"
    assert fact["school_original"] == "北京大学"
    assert fact["state"] == "yes"
    assert fact["source_title"] == "“211工程”学校名单"
    assert fact["source_publisher"] == "中华人民共和国教育部"
    assert fact["document_date"] == "2005-12-23"
    assert fact["other_labels"] == {"985": "not_evaluated", "double_first_class": "not_evaluated"}
    assert fact["scoring_effect"] == "none"
    assert not any("document_number" in key or "文号" in key for key in fact)


def test_projected_fact_miss_remains_unknown():
    repo = ConfirmedProfileRepo("合成未知院校")
    context = read_owner_school_context(repo, student_id=repo.owner)
    facts = project_verified_school_facts(context)

    assert len(facts) == 1
    assert facts[0]["state"] == "unknown"
    assert facts[0]["matched_name"] is None


def test_other_owner_and_unlisted_school_never_become_negative_fact():
    repo = ConfirmedProfileRepo("某普通本科")
    other = read_owner_school_context(repo, student_id="school_owner_other")
    assert other["status"] == "profile_unverified"
    assert other["institution_facts"] == []
    result = read_owner_school_context(repo, student_id=repo.owner)
    assert result["status"] == "school_label_unknown"
    assert result["institution_facts"][0]["state"] == "unknown"
    assert result["profile_version"] == repo.task_id
    assert result["scoring_effect"] == "none"


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_MYSQL=1 for disposable real-MySQL school source probe")
def test_selected_skill_real_mysql_owner_and_d1_rows_unchanged(monkeypatch):
    """The selected Skill reads a synthetic confirmed owner, never D1 writes."""
    import pymysql

    from app.config import Config
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.task_packet import build_standard_packet
    from app.services.db.mysql_client import MySQLClient

    database = f"cpa_school_fact_{uuid.uuid4().hex[:12]}"
    assert database.startswith("cpa_school_fact_") and len(database) == 28
    backend = Path(__file__).resolve().parents[1]
    admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            autocommit=True)
    mysql = None
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        created = True
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=backend, check=True, capture_output=True, text=True,
        )
        mysql = MySQLClient(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            database=database, pool_size=1, max_overflow=0)
        owner = f"school_synthetic_{uuid.uuid4().hex[:8]}"
        task_id = f"resume_synthetic_{uuid.uuid4().hex[:8]}"
        candidate = {"school": "北京大学", "degree": "本科", "major": "计算机科学与技术"}
        profile = {**candidate, "_resume_confirmation": {
            "task_id": task_id, "student_id": owner,
        }}
        with mysql.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO student_profile
                  (student_id,school,degree,major,profile_json,created_at,updated_at)
                VALUES (:sid,:school,:degree,:major,:profile,NOW(),NOW())
            """), {"sid": owner, "school": candidate["school"],
                   "degree": candidate["degree"], "major": candidate["major"],
                   "profile": json.dumps(profile, ensure_ascii=False)})
            conn.execute(text("""
                INSERT INTO pipeline_task
                  (task_id,pipeline,status,progress,result_json,student_id,created_at,updated_at)
                VALUES (:tid,'L0_parse_resume','completed',100,:result,:sid,NOW(),NOW())
            """), {"tid": task_id, "sid": owner,
                   "result": json.dumps({"confirmed": True, "candidate_profile": candidate},
                                        ensure_ascii=False)})
            conn.execute(text("""
                INSERT INTO student_portrait_4d
                  (student_id,d1_score,d2_score,d3_score,d4_score,competitiveness,
                   completeness,portrait_json,created_at,updated_at)
                VALUES (:sid,37,0,0,0,9.25,0.2,:portrait,NOW(),NOW())
            """), {"sid": owner, "portrait": json.dumps({
                "D1_basic": {"sub_school": 37, "dimension_score": 37},
                "status": "partial", "source_confirmation_task_id": task_id,
            }, ensure_ascii=False)})

        def snapshot():
            with mysql.engine.connect() as conn:
                portrait = [tuple(row) for row in conn.execute(text(
                    "SELECT d1_score,portrait_json FROM student_portrait_4d WHERE student_id=:sid"),
                    {"sid": owner})]
                reviews = conn.execute(text(
                    "SELECT COUNT(*) FROM school_tier_review WHERE student_id=:sid"),
                    {"sid": owner}).scalar_one()
                profile_rows = [tuple(row) for row in conn.execute(text(
                    "SELECT profile_json FROM student_profile WHERE student_id=:sid"),
                    {"sid": owner})]
                return portrait, reviews, profile_rows

        before = snapshot()
        systems = []

        class FakeLLM:
            def chat(self, *, messages, **_kwargs):
                systems.append(messages[0]["content"])
                return "已确认学校精确命中所用历史 211 名单；这不是 D1 评分。"

        monkeypatch.setattr("app.services.db.mysql_client.get_mysql_client", lambda: mysql)
        monkeypatch.setattr("app.services.agent_runtime.scoped_invoke.build_gated_tools",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
        selected = scoped_invoke(
            build_standard_packet("qa", "解释学校", skill_ids=["school-context"]),
            Blackboard(), allow_tools=False, interaction_mode=InteractionMode.AGENT,
            student_id=owner,
        )
        assert '"status":"matched_official_name"' in systems[-1]
        assert selected["skill_versions"][0]["profile_version"] == task_id
        assert selected["tool_calls"][0]["status"] == "done"
        assert snapshot() == before

        other = read_owner_school_context(mysql, student_id="unrelated_owner")
        assert other["status"] == "profile_unverified"
        assert other["institution_facts"] == []
        assert snapshot() == before
    finally:
        if mysql is not None:
            mysql.close()
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database}`")
        admin.close()
