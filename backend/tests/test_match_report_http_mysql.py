"""HTTP → real MySQL acceptance for verified L3/L4 source gating.

Opt-in: CPA_TEST_LIVE_MYSQL=1 python -B -m pytest backend/tests/test_match_report_http_mysql.py -q
Synthetic owner, portrait and published job are removed by exact owner/job IDs.
"""

import hashlib
import os
import uuid

import pytest
from flask import g, request
from sqlalchemy import event

from app import create_app
from app.api._helpers import Principal
from app.config import Config
from app.services.db.mysql_client import get_mysql_client
from app.services.job_publication import canonical_json, publish_job
from backend.tests.test_verified_match_report import snapshot


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_http_match_report_source_gate_roundtrip_and_revocation(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    db = get_mysql_client()
    tag = "http_l3l4_" + uuid.uuid4().hex[:16]
    sid, jid, l0, l2 = tag, tag + "_job", tag + "_l0", tag + "_l2"
    source_text = "我负责课程团队项目，和组员沟通协作，在紧急截止日前完成交付。GPA：3.4。"
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    semantic_facts = {
        key: {"value": "中", "evidence": evidence, "source_sha256": source_digest}
        for key, evidence in {
            "communication": "沟通协作", "teamwork": "团队项目",
            "stress_tolerance": "紧急截止日前完成交付", "responsibility": "我负责课程团队项目",
        }.items()
    }
    candidate = {
        "skills": [{"name": "Python", "level": "掌握", "confidence": 1.0}],
        "degree": "专科", "gpa": 3.4, "source_sha256": source_digest,
        "semantic_facts": semantic_facts,
    }
    profile = {**candidate, "_resume_confirmation": {"student_id": sid, "task_id": l0}}
    publication_id = None
    report_id = None

    from app.pipeline.entity_extractor.matcher import SkillMatcher
    monkeypatch.setattr(SkillMatcher, "normalizer", property(lambda self: None))

    try:
        db.execute(
            "INSERT INTO student_profile (student_id,profile_json) VALUES (:sid,:payload)",
            {"sid": sid, "payload": canonical_json(profile)}, commit=True,
        )
        db.execute(
            "INSERT INTO student_portrait_4d (student_id,target_job_id,portrait_json) "
            "VALUES (:sid,:jid,:payload)",
            {"sid": sid, "jid": jid, "payload": canonical_json({
                "source_confirmation_task_id": l0, "source_portrait_task_id": l2,
            })}, commit=True,
        )
        portrait_rows = db.fetch_all(
            "SELECT id FROM student_portrait_4d WHERE student_id=:sid AND target_job_id=:jid",
            {"sid": sid, "jid": jid},
        )
        assert len(portrait_rows) == 1
        portrait_id = str(portrait_rows[0]["id"])
        for task_id, pipeline_name, result in (
            (l0, "L0_parse_resume", {"confirmed": True, "candidate_profile": candidate}),
            (l2, "L2_generate_portrait", {
                "portrait_id": portrait_id,
                "portrait": {"source_confirmation_task_id": l0, "source_portrait_task_id": l2},
            }),
        ):
            db.execute(
                "INSERT INTO pipeline_task "
                "(task_id,pipeline,status,student_id,result_json,params_json) "
                "VALUES (:task,:pipeline,'completed',:sid,:result,'{}')",
                {"task": task_id, "pipeline": pipeline_name, "sid": sid,
                 "result": canonical_json(result)}, commit=True,
            )

        published = publish_job(db, job_portrait_id=jid, version="http-test-v1", snapshot=snapshot())
        publication_id = published["id"]

        app = create_app()

        @app.before_request
        def bind_test_principal():
            owner = request.headers.get("X-Test-Owner", sid)
            g.cpa_principal = Principal(owner, owner, "http-test")

        client = app.test_client()
        match_post = client.post("/api/match/run", json={
            "job_portrait_id": jid, "student_id": "forged-owner", "job_skills": ["forged"],
        })
        assert match_post.status_code == 200
        assert match_post.json["status"] == "partial"
        assert match_post.json["success"] is False
        match_result = match_post.json["data"]["result"]
        assert match_result["status"] == "partial"
        assert match_result["match_id"]
        match_read_url = f"/api/match/result/{sid}/{jid}"
        match_read = client.get(match_read_url)
        assert match_read.status_code == 200
        assert match_read.json["success"] is True
        assert match_read.json["status"] == "partial"
        assert match_read.json["data"]["match_id"] == str(match_result["match_id"])
        assert match_read.json["data"]["source_match_task_id"] == match_result["task_id"]
        assert match_read.json["data"]["sources"] == match_result["sources"]
        assert match_read.json["data"]["is_matched"] in (False, 0)

        # A legacy skill-gap row can coexist with the L3 result. Its read path
        # must not outlive the publication that justified that result.
        db.execute(
            "INSERT INTO skill_gap (student_id,job_portrait_id,gap_json) "
            "VALUES (:sid,:jid,:gaps)",
            {"sid": sid, "jid": jid, "gaps": '[{"skill":"Python"}]'}, commit=True,
        )

        report_post = client.post("/api/report/generate", json={
            "job_portrait_id": jid, "student_id": "forged-owner", "report_id": "forged-report-id",
        })
        assert report_post.status_code == 200
        assert report_post.json["status"] == "partial"
        assert report_post.json["success"] is False
        report_id = report_post.json["data"]["report_id"]
        assert report_id and report_id != "forged-report-id"
        report_read = client.get(f"/api/report/{report_id}")
        assert report_read.status_code == 200
        assert report_read.json["success"] is True
        assert report_read.json["status"] == "partial"

        assert client.get(match_read_url, headers={"X-Test-Owner": tag + "_other"}).status_code == 403
        assert client.get(f"/api/report/{report_id}",
                          headers={"X-Test-Owner": tag + "_other"}).status_code == 404
        match_rows = db.fetch_all(
            "SELECT id,student_id,job_portrait_id,result_json FROM match_result WHERE student_id=:sid",
            {"sid": sid},
        )
        report_rows = db.fetch_all(
            "SELECT report_id,student_id,status,report_json FROM career_report WHERE student_id=:sid",
            {"sid": sid},
        )
        assert len(match_rows) == len(report_rows) == 1
        assert match_rows[0]["job_portrait_id"] == jid
        assert report_rows[0]["report_id"] == report_id
        assert report_rows[0]["status"] == "partial"

        # A terminal task-write failure must not publish a new match snapshot
        # or orphan a fresh report under a failed L3/L4 task.
        original_match = canonical_json(match_rows[0]["result_json"])
        writes_seen = set()
        def fail_partial_task_update(_conn, _cursor, statement, parameters, _context, _many):
            if "INSERT INTO match_result" in statement:
                writes_seen.add("match_result")
            if "INSERT INTO career_report" in statement:
                writes_seen.add("career_report")
            if "UPDATE pipeline_task SET status" in statement and parameters.get("status") == "partial":
                raise RuntimeError("injected terminal task write failure")

        event.listen(db.engine, "before_cursor_execute", fail_partial_task_update)
        try:
            assert client.post("/api/match/run", json={"job_portrait_id": jid}).status_code == 500
            assert client.post("/api/report/generate", json={"job_portrait_id": jid}).status_code == 500
        finally:
            event.remove(db.engine, "before_cursor_execute", fail_partial_task_update)
        assert writes_seen == {"match_result", "career_report"}
        same_match = db.fetch_all(
            "SELECT result_json FROM match_result WHERE student_id=:sid AND job_portrait_id=:jid",
            {"sid": sid, "jid": jid},
        )
        assert len(same_match) == 1
        assert canonical_json(same_match[0]["result_json"]) == original_match
        assert db.fetch_all(
            "SELECT report_id FROM career_report WHERE student_id=:sid", {"sid": sid}
        ) == [{"report_id": report_id}]
        failed_tasks = db.fetch_all(
            "SELECT task_id FROM pipeline_task WHERE student_id=:sid AND status='failed'",
            {"sid": sid},
        )
        assert len(failed_tasks) == 2
        for failed_task in failed_tasks:
            events = db.fetch_all(
                "SELECT status FROM pipeline_task_event WHERE task_id=:task ORDER BY event_id",
                {"task": failed_task["task_id"]},
            )
            assert [row["status"] for row in events] == ["pending", "running", "failed"]

        # Change only the publication state *after* the normal source check,
        # before the domain write. A stale successful task must not be emitted.
        from app.services import pipeline as pipeline_service
        original_sources = pipeline_service.require_authoritative_match_sources

        def revoke_after_source_check(mysql, student_id, job_id, confirmed_profile):
            checked = original_sources(mysql, student_id, job_id, confirmed_profile)
            db.execute(
                "UPDATE job_catalog_publication SET status='revoked' WHERE id=:id",
                {"id": publication_id}, commit=True,
            )
            return checked

        with monkeypatch.context() as isolated:
            isolated.setattr(pipeline_service, "require_authoritative_match_sources",
                             revoke_after_source_check)
            stale_match = client.post("/api/match/run", json={"job_portrait_id": jid})
        assert stale_match.status_code == 200
        assert stale_match.json["data"]["result"]["reason"] == "catalog_publication_unavailable"
        assert canonical_json(db.fetch_all(
            "SELECT result_json FROM match_result WHERE student_id=:sid AND job_portrait_id=:jid",
            {"sid": sid, "jid": jid},
        )[0]["result_json"]) == original_match

        db.execute(
            "UPDATE job_catalog_publication SET status='published' WHERE id=:id",
            {"id": publication_id}, commit=True,
        )
        with monkeypatch.context() as isolated:
            isolated.setattr(pipeline_service, "require_authoritative_match_sources",
                             revoke_after_source_check)
            stale_report = client.post("/api/report/generate", json={"job_portrait_id": jid})
        assert stale_report.status_code == 200
        assert stale_report.json["data"]["report_id"] is None
        assert db.fetch_all(
            "SELECT report_id FROM career_report WHERE student_id=:sid", {"sid": sid}
        ) == [{"report_id": report_id}]

        db.execute(
            "UPDATE job_catalog_publication SET status='revoked' "
            "WHERE id=:id AND job_portrait_id=:jid",
            {"id": publication_id, "jid": jid}, commit=True,
        )
        assert client.get(match_read_url).status_code == 409
        # Legacy list must not return the persisted score after its source was revoked.
        stale_list = client.get(f"/api/match/list/{sid}")
        assert stale_list.status_code == 409
        assert "total_score" not in stale_list.get_data(as_text=True)
        for route in ("explain", "skill-gap"):
            stale_read = client.get(f"/api/match/{route}/{sid}/{jid}")
            assert stale_read.status_code == 409
            assert stale_read.json["status"] == "stale"
            assert "total_score" not in stale_read.get_data(as_text=True)
            assert "Python" not in stale_read.get_data(as_text=True)
        assert client.get(f"/api/report/{report_id}").status_code == 409

        blocked_match = client.post("/api/match/run", json={"job_portrait_id": jid})
        assert blocked_match.status_code == 200
        assert blocked_match.json["success"] is False
        assert blocked_match.json["status"] == "partial"
        assert blocked_match.json["data"]["result"]["reason"] == "catalog_publication_unavailable"
        assert not blocked_match.json["data"]["result"].get("match_id")

        blocked_report = client.post("/api/report/generate", json={"job_portrait_id": jid})
        assert blocked_report.status_code == 200
        assert blocked_report.json["success"] is False
        assert blocked_report.json["status"] == "partial"
        assert blocked_report.json["data"]["report_id"] is None
        assert db.fetch_all("SELECT report_id FROM career_report WHERE student_id=:sid", {"sid": sid}) == [
            {"report_id": report_id}
        ]
    finally:
        db.execute("DELETE FROM career_report WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM skill_gap WHERE student_id=:sid AND job_portrait_id=:jid",
                   {"sid": sid, "jid": jid}, commit=True)
        db.execute("DELETE FROM match_result WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM pipeline_task_event WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM pipeline_task WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM student_portrait_4d WHERE student_id=:sid AND target_job_id=:jid",
                   {"sid": sid, "jid": jid}, commit=True)
        db.execute("DELETE FROM student_profile WHERE student_id=:sid", {"sid": sid}, commit=True)
        if publication_id is not None:
            db.execute("DELETE FROM job_catalog_publication WHERE id=:id AND job_portrait_id=:jid",
                       {"id": publication_id, "jid": jid}, commit=True)
        for table, key, value in (
            ("career_report", "student_id", sid), ("match_result", "student_id", sid),
            ("skill_gap", "student_id", sid),
            ("pipeline_task", "student_id", sid), ("student_profile", "student_id", sid),
            ("pipeline_task_event", "student_id", sid),
            ("student_portrait_4d", "student_id", sid),
            ("job_catalog_publication", "job_portrait_id", jid),
        ):
            residual = db.fetch_all(f"SELECT COUNT(*) AS n FROM {table} WHERE {key}=:value", {"value": value})
            assert residual == [{"n": 0}], (table, residual)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="requires disposable MySQL schema and dedicated Redis test DB")
def test_authenticated_student_job_decision_journey(monkeypatch, tmp_path):
    """A student returns to a confirmed resume, checks a job, then reads evidence.

    This is a synthetic business journey over real loopback HTTP, account
    cookies/CSRF, MySQL and Redis sessions. The published job and L0/L2 source
    rows are synthetic fixtures; the scorer is the real deterministic rule
    implementation. No real job quality, LLM or OCR behavior is asserted.
    """
    import copy
    import json
    from pathlib import Path
    import subprocess
    import sys
    import threading

    import pymysql
    import redis
    import requests
    from werkzeug.serving import make_server

    from app import create_app
    from app.config import Config
    from app.services.account_session_schema import migrate_account_session_schema
    from app.services.db import mysql_client as mysql_module
    from app.services.db.mysql_client import MySQLClient
    from app.services.job_publication import canonical_json, content_sha256, publish_job
    from app.pipeline.entity_extractor.matcher import SkillMatcher

    # Keep the scoring boundary deterministic; the test exercises stored
    # evidence and policy arithmetic, not an external skill normalizer.
    monkeypatch.setattr(SkillMatcher, "normalizer", property(lambda self: None))

    backend = Path(__file__).resolve().parents[1]
    nonce = uuid.uuid4().hex[:12]
    database = f"cpa_job_decision_{nonce}"
    redis_url = os.getenv("CPA_TEST_JOB_DECISION_REDIS_URL", "redis://127.0.0.1:16379/14")
    redis_client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        redis_client.ping()
    except redis.RedisError:
        redis_client.close()
        pytest.skip("dedicated job-decision Redis test DB unavailable")
    if redis_client.dbsize() != 0:
        redis_client.close()
        pytest.skip("job-decision Redis test DB must be empty before the journey")
    baseline_redis_keys = set(redis_client.scan_iter())
    redis_lock = f"cpa-job-decision-test-lock:{nonce}"
    if not redis_client.set(redis_lock, nonce, nx=True, ex=1800):
        redis_client.close()
        pytest.skip("could not reserve job-decision Redis test DB")

    admin = None
    mysql = None
    database_created = False
    server = thread = None
    owner = stranger = None
    try:
        admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                                user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                                autocommit=True)
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        database_created = True
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=backend, check=True, capture_output=True, text=True,
        )
        mysql = MySQLClient(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            database=database, pool_size=3, max_overflow=0)
        monkeypatch.setattr(mysql_module, "_mysql_client", mysql)
        monkeypatch.setattr(Config, "DEMO_MODE", False)
        migrate_account_session_schema(mysql)

        auth_config = type("JobDecisionJourneyConfig", (Config,), {
            "SECRET_KEY": "job-decision-http-test-secret-1234567890-abcdef",
            "DEMO_MODE": False,
            "CPA_AUTH_COOKIE_SECURE": False,
            "REDIS_URL": redis_url,
            "RATELIMIT_KEY_PREFIX": f"job-decision-journey-{nonce}",
            "UPLOAD_FOLDER": str(tmp_path),
            "CORS_ALLOWED_ORIGINS": [],
        })
        app = create_app(auth_config)
        assert app.extensions.get("cpa_account_auth_enabled") is True
        assert app.extensions.get("cpa_account_session_schema_ready") is True
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        owner, stranger = requests.Session(), requests.Session()
        owner.trust_env = stranger.trust_env = False

        anonymous = requests.Session()
        anonymous.trust_env = False
        assert anonymous.get(f"{base}/api/profile/student/workbench", timeout=5).status_code == 401
        assert anonymous.post(f"{base}/api/match/run", json={"job_portrait_id": "not-a-job"},
                              timeout=5).status_code == 401
        assert anonymous.post(f"{base}/api/report/generate", json={"job_portrait_id": "not-a-job"},
                              timeout=5).status_code == 401
        anonymous.close()
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM pipeline_task")["n"] == 0
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM match_result")["n"] == 0
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM career_report")["n"] == 0

        password = "job-decision-test-password-42"

        def register_and_login(session, username):
            token_response = session.get(f"{base}/api/auth/csrf", timeout=5)
            assert token_response.status_code == 200, token_response.text
            csrf = token_response.json()["csrfToken"]
            credentials = {"username": username, "password": password}
            registered = session.post(f"{base}/api/auth/register", json=credentials,
                                      headers={"X-CSRFToken": csrf}, timeout=10)
            assert registered.status_code == 201, registered.text
            account_id = registered.json()["user"]["id"]
            logged_in = session.post(f"{base}/api/auth/login", json=credentials,
                                     headers={"X-CSRFToken": csrf}, timeout=10)
            assert logged_in.status_code == 200, logged_in.text
            csrf = session.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
            assert session.get(f"{base}/api/auth/me", timeout=5).json()["user"]["id"] == account_id
            return account_id, csrf

        owner_id, owner_csrf = register_and_login(owner, f"decision_{nonce}")
        stranger_id, stranger_csrf = register_and_login(stranger, f"decision_other_{nonce}")
        assert owner_id != stranger_id

        source_text = "我负责课程团队项目，和组员沟通协作，在紧急截止日前完成交付。"
        source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        semantic_facts = {
            key: {"value": "中", "evidence": evidence, "source_sha256": source_digest}
            for key, evidence in {
                "communication": "沟通协作", "teamwork": "团队项目",
                "stress_tolerance": "紧急截止日前完成交付", "responsibility": "我负责课程团队项目",
            }.items()
        }
        candidate = {
            "name": "合成学生", "school": "示例大学", "major": "计算机科学", "degree": "本科",
            "skills": [{"name": "Python", "level": "掌握", "confidence": 1.0}],
            "projects": [{"name": "课程团队项目", "role": "负责人",
                          "description": "负责团队协作并在截止日前完成 Python 项目"}],
            "source_sha256": source_digest, "semantic_facts": semantic_facts,
        }
        l0, l2 = f"task_{uuid.uuid4().hex}", f"task_{uuid.uuid4().hex}"

        def seed_confirmed_resume(student_id, confirmation_task_id, *, with_portrait):
            profile = {**candidate, "_resume_confirmation": {
                "student_id": student_id, "task_id": confirmation_task_id,
                "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": []},
            }}
            mysql.execute(
                "INSERT INTO student_profile(student_id,profile_json) VALUES (:sid,:profile)",
                {"sid": student_id, "profile": canonical_json(profile)}, commit=True,
            )
            mysql.execute(
                "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,result_json,params_json) "
                "VALUES (:task,'L0_parse_resume','completed',:sid,:result,'{}')",
                {"task": confirmation_task_id, "sid": student_id,
                 "result": canonical_json({"task_id": confirmation_task_id, "student_id": student_id,
                                            "status": "completed", "confirmed": True,
                                            "candidate_profile": candidate})}, commit=True,
            )
            if not with_portrait:
                return
            portrait_task_id = l2 if student_id == owner_id else f"task_{uuid.uuid4().hex}"
            portrait = {
                "source_confirmation_task_id": confirmation_task_id,
                "source_portrait_task_id": portrait_task_id,
                "source_self_report_revision": 0,
                "D1_basic": {"dimension_score": 67},
                "D2_skill": {"dimension_score": 72},
                "D3_quality": {"dimension_score": 61},
                "D4_potential": {"dimension_score": 58, "evidence": ["课程团队项目"]},
            }
            mysql.execute(
                "INSERT INTO student_portrait_4d(student_id,target_job_id,portrait_json) "
                "VALUES (:sid,NULL,:portrait)",
                {"sid": student_id, "portrait": canonical_json(portrait)}, commit=True,
            )
            portrait_row = mysql.fetch_one(
                "SELECT id FROM student_portrait_4d WHERE student_id=:sid AND target_job_id IS NULL",
                {"sid": student_id},
            )
            portrait_id = str(portrait_row["id"])
            mysql.execute(
                "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,result_json,params_json) "
                "VALUES (:task,'L2_generate_portrait','completed',:sid,:result,'{}')",
                {"task": portrait_task_id, "sid": student_id,
                 "result": canonical_json({"task_id": portrait_task_id, "student_id": student_id,
                                            "status": "completed", "portrait_id": portrait_id,
                                            "resource_type": "student_portrait_4d", "resource_id": portrait_id,
                                            "source_confirmation_task_id": confirmation_task_id,
                                            "portrait": portrait})}, commit=True,
            )

        job_id = f"job_{uuid.uuid4().hex[:20]}"
        unknown_job_id = f"job_{uuid.uuid4().hex[:20]}"
        seed_confirmed_resume(owner_id, l0, with_portrait=True)
        stranger_l0 = f"task_{uuid.uuid4().hex}"
        seed_confirmed_resume(stranger_id, stranger_l0, with_portrait=False)
        partial_l2 = f"task_{uuid.uuid4().hex}"
        partial_portrait = {
            "source_confirmation_task_id": stranger_l0,
            "source_portrait_task_id": partial_l2,
            "source_self_report_revision": 0,
            "D1_basic": {"dimension_score": 65},
            "D2_skill": {"dimension_score": 70},
            "D3_quality": {"dimension_score": None},
            "D4_potential": {"dimension_score": None, "evidence": []},
        }
        mysql.execute(
            "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,result_json,params_json) "
            "VALUES (:task,'L2_generate_portrait','partial',:sid,:result,'{}')",
            {"task": partial_l2, "sid": stranger_id,
             "result": canonical_json({"task_id": partial_l2, "student_id": stranger_id,
                                        "status": "partial", "portrait_id": None,
                                        "resource_type": "pipeline_task", "resource_id": partial_l2,
                                        "source_confirmation_task_id": stranger_l0,
                                        "portrait_candidate": partial_portrait,
                                        "missing_fields": ["D3_quality", "D4_potential"]})}, commit=True,
        )

        known_job = copy.deepcopy(snapshot())
        publication = publish_job(mysql, job_portrait_id=job_id,
                                  version=f"decision-v1-{nonce}", snapshot=known_job)
        assert publication["content_sha256"] == content_sha256(known_job)
        unknown_job = copy.deepcopy(snapshot())
        unknown_job["required_skills"] = []
        unknown_job["field_evidence"]["required_skills"] = {"status": "not_stated", "excerpt": None}
        unknown_publication = publish_job(
            mysql, job_portrait_id=unknown_job_id, version=f"decision-unknown-{nonce}", snapshot=unknown_job)
        assert unknown_publication["content_sha256"] == content_sha256(unknown_job)

        # The student returns to the platform: current resume and job directory
        # are read before the student chooses one posted role.
        workbench = owner.get(f"{base}/api/profile/student/workbench", timeout=10)
        assert workbench.status_code == 200, workbench.text
        workbench_state = workbench.json()["data"]
        assert workbench_state["source_confirmation_task_id"] == l0
        assert workbench_state["portrait"]["task_id"] == l2
        assert workbench_state["portrait"]["scoring_eligible"] is True
        directory = owner.get(f"{base}/api/profile/job/list", timeout=10)
        assert directory.status_code == 200, directory.text
        jobs = directory.json()["data"]
        assert any(item.get("portrait_id") == job_id for item in jobs)
        selected_job = owner.get(f"{base}/api/profile/job/{job_id}", timeout=10)
        assert selected_job.status_code == 200, selected_job.text
        selected = selected_job.json()["data"]
        assert selected["publication_version"] == publication["publication_version"]
        assert selected["content_sha256"] == publication["content_sha256"]
        assert selected["portrait_json"]["position_name"] == known_job["position_name"]

        # One changed field at a time is deliberately not trusted: owner comes
        # from the real cookie, and skills/decision inputs come from sources.
        match_post = owner.post(f"{base}/api/match/run", json={
            "job_portrait_id": job_id, "student_id": stranger_id, "job_skills": ["ForgedSkill"],
        }, headers={"X-CSRFToken": owner_csrf}, timeout=20)
        assert match_post.status_code == 200, match_post.text
        match_body = match_post.json()
        assert match_body["status"] == "partial" and match_body["success"] is False
        match_outcome = match_body["data"]["result"]
        assert match_outcome["status"] == "partial" and match_outcome["verified"] is True, match_outcome
        match_id = str(match_outcome["match_id"])
        match_task_id = match_outcome["task_id"]
        assert match_body["data"]["task_id"] == match_task_id
        assert match_outcome["score_kind"] == "policy_estimate"
        assert match_outcome["reason"] == "scoring_policy_not_validated"
        assert match_outcome["next_action"] == "validate_scoring_policy"

        match_row = mysql.fetch_one(
            "SELECT id,student_id,job_portrait_id,result_json FROM match_result "
            "WHERE student_id=:sid AND job_portrait_id=:jid",
            {"sid": owner_id, "jid": job_id},
        )
        assert match_row is not None and str(match_row["id"]) == match_id
        match_task = mysql.fetch_one(
            "SELECT task_id,student_id,pipeline,status,result_json FROM pipeline_task WHERE task_id=:task",
            {"task": match_task_id},
        )
        assert match_task["student_id"] == owner_id
        assert match_task["pipeline"] == "L3_run_match" and match_task["status"] == "partial"
        match_task_result = match_task["result_json"]
        match_task_result = json.loads(match_task_result) if isinstance(match_task_result, str) else match_task_result
        assert str(match_task_result["match_id"]) == match_id
        match_snapshot = match_row["result_json"]
        match_snapshot = json.loads(match_snapshot) if isinstance(match_snapshot, str) else match_snapshot
        assert match_row["student_id"] == owner_id and match_row["job_portrait_id"] == job_id
        assert match_snapshot["score_kind"] == "policy_estimate"
        assert match_snapshot["quality_status"] == "not_validated"
        assert match_snapshot["job_snapshot"]["required_skills"] == known_job["required_skills"]
        assert "ForgedSkill" not in canonical_json(match_snapshot)
        assert match_snapshot["sources"]["source_confirmation_task_id"] == l0
        assert match_snapshot["sources"]["source_portrait_task_id"] == l2
        portrait_row = mysql.fetch_one(
            "SELECT id,student_id,portrait_json FROM student_portrait_4d "
            "WHERE student_id=:sid AND target_job_id IS NULL", {"sid": owner_id},
        )
        assert portrait_row["student_id"] == owner_id
        assert match_snapshot["sources"]["source_portrait_id"] == str(portrait_row["id"])
        assert match_snapshot["sources"]["source_publication_id"] == str(publication["id"])
        assert match_snapshot["sources"]["source_publication_version"] == publication["publication_version"]
        assert match_snapshot["sources"]["source_publication_sha256"] == publication["content_sha256"]
        match_read = owner.get(f"{base}/api/match/result/{owner_id}/{job_id}", timeout=10)
        assert match_read.status_code == 200, match_read.text
        assert match_read.json()["status"] == "partial"
        assert match_read.json()["data"]["score_kind"] == "policy_estimate"
        assert match_read.json()["data"]["quality_status"] == "not_validated"
        assert match_read.json()["data"]["position_name"] == known_job["position_name"]

        report_post = owner.post(f"{base}/api/report/generate", json={
            "job_portrait_id": job_id, "student_id": stranger_id, "report_id": "forged-report",
        }, headers={"X-CSRFToken": owner_csrf}, timeout=20)
        assert report_post.status_code == 200, report_post.text
        report_body = report_post.json()
        assert report_body["status"] == "partial" and report_body["success"] is False
        report_id = report_body["data"]["report_id"]
        report_task_id = report_body["data"]["task_id"]
        assert report_id and report_id != "forged-report"
        report_row = mysql.fetch_one(
            "SELECT report_id,student_id,job_portrait_id,status,report_json FROM career_report "
            "WHERE report_id=:rid AND student_id=:sid", {"rid": report_id, "sid": owner_id},
        )
        assert report_row is not None and report_row["job_portrait_id"] == job_id
        assert report_row["status"] == "partial"
        report_task = mysql.fetch_one(
            "SELECT task_id,student_id,pipeline,status,result_json FROM pipeline_task WHERE task_id=:task",
            {"task": report_task_id},
        )
        assert report_task["student_id"] == owner_id
        assert report_task["pipeline"] == "L4_generate_report" and report_task["status"] == "partial"
        report_task_result = report_task["result_json"]
        report_task_result = json.loads(report_task_result) if isinstance(report_task_result, str) else report_task_result
        assert report_task_result["report_id"] == report_id
        report_snapshot = report_row["report_json"]
        report_snapshot = json.loads(report_snapshot) if isinstance(report_snapshot, str) else report_snapshot
        assert report_snapshot["sources"]["match_id"] == match_id
        assert report_snapshot["sources"]["source_publication_id"] == str(publication["id"])
        assert report_snapshot["sources"]["source_publication_version"] == publication["publication_version"]
        assert report_snapshot["sources"]["source_publication_sha256"] == publication["content_sha256"]
        assert report_snapshot["missing_sections"] == [
            "validated_scoring_policy", "evidence_based_action_plan", "verified_resource_recommendations"]
        report_read = owner.get(f"{base}/api/report/{report_id}", timeout=10)
        assert report_read.status_code == 200, report_read.text
        assert report_read.json()["status"] == "partial"
        assert report_read.json()["data"]["report_id"] == report_id
        assert report_read.json()["data"]["position_name"] == known_job["position_name"]
        assert report_read.json()["data"]["report_json"]["sources"]["match_id"] == match_id

        # Another logged-in account owns a confirmed resume, but no eligible L2.
        # Forging the owner in the request must not borrow the first student's proof.
        partial_workbench = stranger.get(f"{base}/api/profile/student/workbench", timeout=10)
        assert partial_workbench.status_code == 200, partial_workbench.text
        partial_state = partial_workbench.json()["data"]["portrait"]
        assert partial_state["state"] == "ready_with_unknowns"
        assert partial_state["scoring_eligible"] is False
        assert partial_state["dimensions"]["D3"] is None and partial_state["dimensions"]["D4"] is None
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM student_portrait_4d WHERE student_id=:sid",
                               {"sid": stranger_id})["n"] == 0
        blocked = stranger.post(f"{base}/api/match/run", json={
            "job_portrait_id": job_id, "student_id": owner_id,
        }, headers={"X-CSRFToken": stranger_csrf}, timeout=20)
        assert blocked.status_code == 200, blocked.text
        blocked_outcome = blocked.json()["data"]["result"]
        assert blocked.json()["status"] == "partial"
        assert blocked_outcome["reason"] == "confirmed_portrait_unavailable"
        assert blocked_outcome["next_action"] == "generate_portrait"
        assert not blocked_outcome.get("match_id")
        blocked_report = stranger.post(f"{base}/api/report/generate", json={
            "job_portrait_id": job_id, "student_id": owner_id, "report_id": "forged-owner-report",
        }, headers={"X-CSRFToken": stranger_csrf}, timeout=20)
        assert blocked_report.status_code == 200, blocked_report.text
        assert blocked_report.json()["status"] == "partial"
        assert blocked_report.json()["data"]["report_id"] is None
        assert blocked_report.json()["data"]["reason"] == "confirmed_portrait_unavailable"
        assert blocked_report.json()["data"]["next_action"] == "generate_portrait"
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM match_result WHERE student_id=:sid",
                               {"sid": stranger_id})["n"] == 0
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM career_report WHERE student_id=:sid",
                               {"sid": stranger_id})["n"] == 0

        # The directory may show a reviewed job with unresolved requirements;
        # the hard gate must still block scoring/report creation with a clear action.
        unknown_read = owner.get(f"{base}/api/profile/job/{unknown_job_id}", timeout=10)
        assert unknown_read.status_code == 200, unknown_read.text
        assert unknown_read.json()["data"]["content_sha256"] == unknown_publication["content_sha256"]
        unknown_match = owner.post(f"{base}/api/match/run", json={"job_portrait_id": unknown_job_id},
                                   headers={"X-CSRFToken": owner_csrf}, timeout=20)
        assert unknown_match.status_code == 200, unknown_match.text
        assert unknown_match.json()["data"]["result"]["reason"] == "catalog_requirements_unknown"
        assert unknown_match.json()["data"]["result"]["next_action"] == "review_job_requirements"
        assert not unknown_match.json()["data"]["result"].get("match_id")
        unknown_report = owner.post(f"{base}/api/report/generate",
                                    json={"job_portrait_id": unknown_job_id},
                                    headers={"X-CSRFToken": owner_csrf}, timeout=20)
        assert unknown_report.status_code == 200, unknown_report.text
        assert unknown_report.json()["data"]["report_id"] is None
        assert unknown_report.json()["data"]["reason"] == "catalog_requirements_unknown"
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM match_result WHERE student_id=:sid AND job_portrait_id=:jid",
                               {"sid": owner_id, "jid": unknown_job_id})["n"] == 0
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM career_report WHERE student_id=:sid AND job_portrait_id=:jid",
                               {"sid": owner_id, "jid": unknown_job_id})["n"] == 0

        # Cross-account reads are owner-bound; neither report nor match content leaks.
        assert stranger.get(f"{base}/api/match/result/{owner_id}/{job_id}", timeout=10).status_code == 403
        assert stranger.get(f"{base}/api/report/{report_id}", timeout=10).status_code == 404
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM match_result WHERE student_id=:sid",
                               {"sid": owner_id})["n"] == 1
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM career_report WHERE student_id=:sid",
                               {"sid": owner_id})["n"] == 1

        # Withdrawal invalidates persisted outputs; then the now-empty directory
        # and next-action block are read from the same actual HTTP journey.
        mysql.execute("UPDATE job_catalog_publication SET status='revoked' WHERE id IN (:known,:unknown)",
                      {"known": publication["id"], "unknown": unknown_publication["id"]}, commit=True)
        empty_directory = owner.get(f"{base}/api/profile/job/list", timeout=10)
        assert empty_directory.status_code == 200, empty_directory.text
        assert empty_directory.json()["status"] == "empty"
        assert empty_directory.json()["data"] == []
        stale_match = owner.get(f"{base}/api/match/result/{owner_id}/{job_id}", timeout=10)
        stale_report = owner.get(f"{base}/api/report/{report_id}", timeout=10)
        assert stale_match.status_code == 409 and stale_match.json()["status"] == "stale"
        assert stale_report.status_code == 409 and stale_report.json()["status"] == "stale"
        assert "total_score" not in stale_match.text
        assert "match_snapshot" not in stale_report.text
        withdrawn_match = owner.post(f"{base}/api/match/run", json={"job_portrait_id": job_id},
                                     headers={"X-CSRFToken": owner_csrf}, timeout=20)
        assert withdrawn_match.status_code == 200, withdrawn_match.text
        assert withdrawn_match.json()["data"]["result"]["reason"] == "catalog_publication_unavailable"
        assert withdrawn_match.json()["data"]["result"]["next_action"] == "publish_verified_job_catalog"
        assert not withdrawn_match.json()["data"]["result"].get("match_id")
        withdrawn_report = owner.post(f"{base}/api/report/generate", json={"job_portrait_id": job_id},
                                      headers={"X-CSRFToken": owner_csrf}, timeout=20)
        assert withdrawn_report.status_code == 200, withdrawn_report.text
        assert withdrawn_report.json()["data"]["report_id"] is None
        assert withdrawn_report.json()["data"]["reason"] == "catalog_publication_unavailable"
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM match_result WHERE student_id=:sid",
                               {"sid": owner_id})["n"] == 1
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM career_report WHERE student_id=:sid",
                               {"sid": owner_id})["n"] == 1

        # Every owned output has exactly one matching authoritative business row.
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM pipeline_task WHERE student_id=:sid AND pipeline='L3_run_match'",
                               {"sid": owner_id})["n"] == 3
        assert mysql.fetch_one("SELECT COUNT(*) AS n FROM pipeline_task WHERE student_id=:sid AND pipeline='L4_generate_report'",
                               {"sid": owner_id})["n"] == 3
    finally:
        if owner:
            owner.close()
        if stranger:
            stranger.close()
        if server:
            server.shutdown()
        if thread:
            thread.join(timeout=5)
        try:
            if mysql:
                mysql.close()
            if database_created and admin:
                with admin.cursor() as cursor:
                    cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
                    cursor.execute(
                        "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=%s",
                        (database,),
                    )
                    assert cursor.fetchone() is None
        finally:
            if admin:
                admin.close()
            # The logical DB was required to be empty. Remove only keys created
            # by this journey; never FLUSHDB or touch the developer's Redis DB.
            try:
                created_keys = list(set(redis_client.scan_iter()) - baseline_redis_keys)
                if created_keys:
                    redis_client.delete(*created_keys)
                assert set(redis_client.scan_iter()) == baseline_redis_keys
            finally:
                redis_client.close()
