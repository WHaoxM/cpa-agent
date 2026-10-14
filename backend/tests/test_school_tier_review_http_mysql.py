"""Official school-tier approval: local HTTP plus a disposable migrated MySQL.

The real source fetch is replaced only after URI validation; the separate
network test below checks the TLS-pinned fetch against Tsinghua's live page.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone

import pytest
import requests
from werkzeug.serving import make_server


BACKEND = Path(__file__).resolve().parents[1]
URI = "https://www.tsinghua.edu.cn/xxgk/xxyg.htm"
QUOTE = "通过实施“211工程”“985工程”，开展“双一流”建设，清华大学"


def test_school_source_rejects_other_school_http_private_ip_and_redirect(monkeypatch):
    from app.services import school_tier_review as review

    with pytest.raises(ValueError):
        review._checked_uri(URI, school_name="北京大学", source_kind="school_official")
    with pytest.raises(ValueError):
        review._checked_uri(URI.replace("https://", "http://"),
                            school_name="清华大学", source_kind="school_official")
    with pytest.raises(ValueError):
        review._checked_uri("https://127.0.0.1/", school_name="清华大学",
                            source_kind="school_official")
    monkeypatch.setattr(review.socket, "getaddrinfo",
                        lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(review.SchoolTierSourceUnavailable, match="公网"):
        review.fetch_official_source(URI, school_name="清华大学", source_kind="school_official")


def test_corrupt_persisted_source_is_unavailable_not_client_input_error():
    from app.services import school_tier_review as review

    row = {"id": 7, "student_id": "owner", "confirmation_task_id": "l0",
           "school_name": "清华大学", "school_tier": "985",
           "source_kind": "school_official", "source_uri": "https://127.0.0.1/",
           "source_sha256": "a" * 64, "source_quote": QUOTE,
           "quote_sha256": hashlib.sha256(QUOTE.encode()).hexdigest(),
           "reviewed_by": "admin", "status": "approved"}
    row["evidence_sha256"] = review._evidence_digest(row)
    with pytest.raises(review.SchoolTierSourceUnavailable, match="来源失效"):
        review._checked_review(row)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="explicit opt-in for disposable MySQL and Redis")
def test_school_review_http_l2_revoke_blocks_stale_portrait(monkeypatch):
    import pymysql
    from app import create_app
    from app.config import Config
    from app.pipeline.entity_extractor.resume_structure import _ground_semantic_facts
    from app.services import personal_graph_jobs, school_tier_review
    from app.services.db import mysql_client
    from app.services.pipeline import confirm_resume_candidate

    nonce = uuid.uuid4().hex[:12]
    db_name = "cpa_school_review_" + nonce
    admin_db = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                               user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                               autocommit=True)
    server = thread = None
    admin = student = None
    isolated_client = None
    try:
        with admin_db.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4")
        subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
                        "-x", f"database={db_name}", "upgrade", "0004_resume_trace_schema"],
                       cwd=BACKEND, check=True, capture_output=True, text=True)
        isolated_client = mysql_client.MySQLClient(database=db_name)
        monkeypatch.setattr(mysql_client, "_mysql_client", isolated_client)
        monkeypatch.setattr(Config, "MYSQL_DATABASE", db_name)
        monkeypatch.setattr(Config, "DEMO_MODE", False)
        monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                            lambda *_a, **_k: {"status": "not_requested"})
        # The official response is simulated, not the source safety gate.
        monkeypatch.setattr(school_tier_review, "fetch_official_source",
                            lambda uri, *, school_name, source_kind: {
                                "source_sha256": hashlib.sha256(QUOTE.encode()).hexdigest(),
                                "visible_text": QUOTE,
                                "source_checked_at": datetime.now(timezone.utc).replace(tzinfo=None),
                            })
        config = type("SchoolReviewConfig", (Config,), {
            "SECRET_KEY": "school-review-test-secret-12345678901234567890",
            "DEMO_MODE": False, "CPA_AUTH_COOKIE_SECURE": False,
            "RATELIMIT_KEY_PREFIX": "school-review-" + nonce,
        })
        app = create_app(config)
        password = "strong-school-review-password-42"
        admin_name, student_name = "school_admin_" + nonce, "school_student_" + nonce
        provisioned = app.test_cli_runner().invoke(
            args=["provision-admin", "--username", admin_name],
            input=f"{password}\n{password}\n")
        assert provisioned.exit_code == 0, provisioned.output
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        admin, student = requests.Session(), requests.Session()
        admin.trust_env = student.trust_env = False

        def csrf(session):
            response = session.get(base + "/api/auth/csrf", timeout=5)
            assert response.status_code == 200, response.text
            return {"X-CSRFToken": response.json()["csrfToken"]}

        assert admin.post(base + "/api/auth/login",
                          json={"username": admin_name, "password": password},
                          headers=csrf(admin), timeout=5).status_code == 200
        assert student.post(base + "/api/auth/register",
                            json={"username": student_name, "password": password},
                            headers=csrf(student), timeout=5).status_code == 201
        assert student.post(base + "/api/auth/login",
                            json={"username": student_name, "password": password},
                            headers=csrf(student), timeout=5).status_code == 200
        sid = isolated_client.execute_single(
            "SELECT id FROM user_account WHERE username=:name", {"name": student_name})["id"]
        def confirmed_candidate(tier):
            task_id = "task_" + uuid.uuid4().hex[:20]
            source = (f"学校名称清华大学；学校层级{tier}；专业计算机科学与技术；"
                      "沟通强；团队强；抗压强；负责强")
            candidate = _ground_semantic_facts({
                "school": "清华大学", "major": "计算机科学与技术", "degree": "本科",
                "projects": [{"name": "项目"}], "skills": ["Python"],
            }, {"semantic_facts": {
                "school_tier": {"value": tier, "evidence": tier},
                "major_category": {"value": "计算机科学与技术", "evidence": "计算机科学与技术"},
                "communication": {"value": "强", "evidence": "沟通强"},
                "teamwork": {"value": "强", "evidence": "团队强"},
                "stress_tolerance": {"value": "强", "evidence": "抗压强"},
                "responsibility": {"value": "强", "evidence": "负责强"},
            }}, source)
            isolated_client.execute(
                "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,params_json,result_json) "
                "VALUES (:task,'L0_parse_resume','needs_input',:sid,'{}',:result)",
                {"task": task_id, "sid": sid, "result": json.dumps({
                    "task_id": task_id, "student_id": sid, "status": "needs_input", "confirmed": False,
                    "candidate_profile": candidate, "structure_validation": {"ok": True},
                    "source_evidence": {"missing_pages": [], "blocks": [
                        {"block_id": "b1", "page": 1, "text": source}]},
                }, ensure_ascii=False)}, commit=True)
            assert confirm_resume_candidate(isolated_client, student_id=sid,
                                            task_id=task_id)["status"] == "completed"
            return task_id

        legacy_l0 = confirmed_candidate("985")
        legacy_l2 = student.post(base + "/api/profile/student/pipeline",
                                 json={"pipeline": "L2_generate_portrait"},
                                 headers=csrf(student), timeout=15)
        assert legacy_l2.status_code == 200, legacy_l2.text
        assert legacy_l2.json()["data"]["status"] == "completed", legacy_l2.text
        assert legacy_l2.json()["data"]["portrait"]["school_tier_review"] is None
        assert student.get(base + f"/api/profile/student/{sid}/4d", timeout=5).status_code == 200

        l0 = confirmed_candidate("其他理工")
        assert l0 != legacy_l0
        assert student.get(base + f"/api/profile/student/{sid}/4d", timeout=5).status_code == 409

        # Current live deployments may not yet have the new table. Existing L2
        # must retain its honest partial result; admin review must not claim 404.
        before_migration = student.post(base + "/api/profile/student/pipeline",
                                        json={"pipeline": "L2_generate_portrait"},
                                        headers=csrf(student), timeout=15)
        assert before_migration.status_code == 200, before_migration.text
        assert before_migration.json()["data"]["status"] == "partial"
        unavailable = admin.get(base + "/api/admin/school-tier-reviews/current",
                                params={"student_id": sid, "confirmation_task_id": l0}, timeout=5)
        assert unavailable.status_code == 503, unavailable.text
        subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
                        "-x", f"database={db_name}", "upgrade", "head"],
                       cwd=BACKEND, check=True, capture_output=True, text=True)

        body = {"student_id": sid, "confirmation_task_id": l0,
                "school_name": "清华大学", "school_tier": "985",
                "source_kind": "school_official", "source_uri": URI,
                "source_quote": QUOTE, "confirm_official_evidence": True}
        route = base + "/api/admin/school-tier-reviews"
        assert student.post(route, json=body, headers=csrf(student), timeout=5).status_code == 403
        stale_l0 = {**body, "confirmation_task_id": legacy_l0}
        assert admin.post(route, json=stale_l0, headers=csrf(admin), timeout=5).status_code == 409
        bad = {**body, "source_quote": "清华大学通过实施985工程"}
        assert admin.post(route, json=bad, headers=csrf(admin), timeout=5).status_code == 409
        approved = admin.post(route, json=body, headers=csrf(admin), timeout=5)
        assert approved.status_code == 201, approved.text
        review = approved.json()["data"]
        assert review["status"] == "approved"
        repeated = admin.post(route, json=body, headers=csrf(admin), timeout=5)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["data"]["id"] == review["id"]
        current = admin.get(route + "/current",
                            params={"student_id": sid, "confirmation_task_id": l0}, timeout=5)
        assert current.status_code == 200, current.text
        assert current.json()["data"]["evidence_sha256"] == review["evidence_sha256"]
        assert isolated_client.execute_single(
            "SELECT COUNT(*) AS n FROM admin_audit_log WHERE target_type='school_tier_review'"
        )["n"] == 1
        original = isolated_client.execute_single(
            "SELECT profile_json FROM student_profile WHERE student_id=:sid", {"sid": sid})
        profile = json.loads(original["profile_json"])
        assert profile["semantic_facts"]["school_tier"]["value"] == "其他理工"

        generated = student.post(base + "/api/profile/student/pipeline",
                                 json={"pipeline": "L2_generate_portrait"},
                                 headers=csrf(student), timeout=15)
        assert generated.status_code == 200, generated.text
        assert generated.json()["data"]["status"] == "completed", generated.text
        portrait = generated.json()["data"]["portrait"]
        assert portrait["D1_basic"]["school_tier"] == "985"
        assert portrait["D1_basic"]["school_score_basis"] == "reviewed_official_school_tier"
        assert portrait["school_tier_review"] == {
            "id": review["id"], "evidence_sha256": review["evidence_sha256"]}
        assert student.get(base + f"/api/profile/student/{sid}/4d", timeout=5).status_code == 200

        wrong_version = admin.post(route + f"/{review['id']}/revoke",
                                   json={"expected_evidence_sha256": "0" * 64,
                                         "reason": "审核证据版本已过期，不能撤回"},
                                   headers=csrf(admin), timeout=5)
        assert wrong_version.status_code == 409, wrong_version.text
        revoked = admin.post(route + f"/{review['id']}/revoke",
                             json={"expected_evidence_sha256": review["evidence_sha256"],
                                   "reason": "官方来源审核撤回，需重新核验"},
                             headers=csrf(admin), timeout=5)
        assert revoked.status_code == 200, revoked.text
        assert student.get(base + f"/api/profile/student/{sid}/4d", timeout=5).status_code == 409
        for level in ("L3_run_match", "L4_generate_report"):
            response = student.post(base + "/api/profile/student/pipeline",
                                    json={"pipeline": level, "job_portrait_id": "missing_job"},
                                    headers=csrf(student), timeout=10)
            assert response.status_code == 200, response.text
            assert response.json()["data"]["status"] == "partial", response.text
            assert response.json()["data"]["reason"] == "confirmed_portrait_unavailable"
        assert isolated_client.execute_single(
            "SELECT COUNT(*) AS n FROM admin_audit_log WHERE target_type='school_tier_review'"
        )["n"] == 2
        assert isolated_client.execute_single(
            "SELECT COUNT(*) AS n FROM match_result WHERE student_id=:sid", {"sid": sid})["n"] == 0
        assert isolated_client.execute_single(
            "SELECT COUNT(*) AS n FROM career_report WHERE student_id=:sid", {"sid": sid})["n"] == 0
    finally:
        for session in (admin, student):
            if session:
                session.close()
        if server:
            server.shutdown()
        if thread:
            thread.join(timeout=5)
        if isolated_client:
            isolated_client.close()
        with admin_db.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        admin_db.close()
