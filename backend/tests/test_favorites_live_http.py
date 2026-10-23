"""Opt-in-by-environment live HTTP + MySQL acceptance for secondary favorites routes.

Requires the README's local MySQL container at localhost:13306. Test rows use a
unique owner and are deleted without touching pre-existing user data.
"""

import json
import hashlib
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

import pytest
from werkzeug.serving import make_server

from app import create_app
from app.config import Config
from app.services.db import get_mysql_client


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_MYSQL=1 for disposable real-MySQL favorites test")
def test_catalog_publication_favorite_real_mysql_is_owner_bound_and_historical(monkeypatch):
    """Exercise version-bound save/read/revoke/delete in a disposable schema."""
    import pymysql
    from flask import Flask, g, request
    from app.api import favorites_bp
    from app.api._helpers import Principal
    from app.config import Config
    from app.services.db.mysql_client import MySQLClient

    name = "cpa_saved_job_" + uuid.uuid4().hex[:12]
    admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            autocommit=True)
    mysql = None
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        mysql = MySQLClient(database=name)
        mysql.execute_write("""
            CREATE TABLE job_catalog_publication (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                job_portrait_id VARCHAR(64) NOT NULL,
                publication_version VARCHAR(64) NOT NULL,
                snapshot_json JSON NOT NULL,
                content_sha256 CHAR(64) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'published',
                published_at DATETIME NOT NULL,
                UNIQUE KEY uk_job_publication (job_portrait_id, publication_version),
                INDEX idx_job_publication (job_portrait_id, id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        mysql.execute_write("""
            CREATE TABLE saved_job (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                job_id VARCHAR(64) NOT NULL,
                job_title VARCHAR(200) NULL,
                company VARCHAR(200) NULL,
                industry VARCHAR(100) NULL,
                salary VARCHAR(100) NULL,
                location VARCHAR(200) NULL,
                match_score FLOAT NULL,
                required_skills JSON NULL,
                saved_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                publication_id BIGINT NULL,
                UNIQUE KEY uk_saved_job_owner_publication (user_id, publication_id),
                CONSTRAINT fk_saved_job_publication FOREIGN KEY (publication_id)
                    REFERENCES job_catalog_publication(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        now = datetime.now(timezone.utc)
        source = "数据分析实习生岗位。要求本科及以上。专业不限。暂无证书要求。技能要求 Python。"
        snapshot = {
            "position_name": "数据分析实习生",
            "source_uri": f"https://jobs.example.org/roles/{name}",
            "source_text": source,
            "source_text_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "reviewed_by": "admin_fixture",
            "reviewed_at": (now - timedelta(minutes=1)).isoformat(),
            "source_fetched_at": (now - timedelta(minutes=2)).isoformat(),
            "source_license": "operator-supplied",
            "review_state": "approved",
            "required_skills": [{"name": "Python", "level": "必须"}],
            "job_profile": {"degree_requirement": "本科及以上", "major_requirements": [],
                            "major_strict": False, "required_certs": []},
            "field_evidence": {
                "position_name": {"status": "explicit", "excerpt": "数据分析实习生"},
                "degree_requirement": {"status": "explicit", "excerpt": "本科及以上"},
                "major_requirements": {"status": "explicit", "excerpt": "专业不限"},
                "major_strict": {"status": "explicit", "excerpt": "专业不限"},
                "required_certs": {"status": "explicit", "excerpt": "暂无证书要求"},
                "required_skills": {"status": "explicit", "excerpt": "技能要求 Python"},
            },
        }
        from app.services.job_publication import canonical_json, content_sha256
        digest = content_sha256(snapshot)
        mysql.execute_write(
            "INSERT INTO job_catalog_publication(job_portrait_id,publication_version,snapshot_json,"
            "content_sha256,status,published_at) VALUES (:jid,'v1',:snapshot,:digest,'published',NOW())",
            {"jid": name, "snapshot": canonical_json(snapshot), "digest": digest},
        )
        publication_id = mysql.execute_single(
            "SELECT id FROM job_catalog_publication WHERE job_portrait_id=:jid", {"jid": name}
        )["id"]

        app = Flask("saved-job-publication-http-test")
        app.register_blueprint(favorites_bp, url_prefix="/api")
        active_owner = {"id": "favorite_owner"}

        @app.before_request
        def identity():
            owner = request.headers.get("X-Test-Student", active_owner["id"])
            g.cpa_principal = Principal(owner, owner, "test")

        monkeypatch.setattr("app.repositories.favorites_repo.get_mysql_client", lambda: mysql)
        server = make_server("127.0.0.1", 0, app, threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def request_json(method, path, owner="favorite_owner", payload=None):
            body = None if payload is None else json.dumps(payload).encode("utf-8")
            headers = {"X-Test-Student": owner}
            if body is not None:
                headers["Content-Type"] = "application/json"
            req = Request(base + path, data=body, headers=headers, method=method)
            with urlopen(req, timeout=8) as response:
                return response.status, json.load(response)

        try:
            ref = {"job_portrait_id": name, "publication_version": "v1", "content_sha256": digest}
            stale_ref = {**ref, "content_sha256": "0" * 64}
            with pytest.raises(Exception) as stale_digest:
                request_json("POST", "/api/saved-jobs", payload={"publication_ref": stale_ref})
            assert getattr(stale_digest.value, "code", None) == 409
            assert mysql.fetch_all("SELECT id FROM saved_job") == []
            forged = {"publication_ref": ref, "job_title": "伪造岗位", "salary": "100K",
                      "match_score": 100, "required_skills": ["伪造技能"], "user_id": "victim"}
            status, saved = request_json("POST", "/api/saved-jobs", payload=forged)
            assert status == 200 and saved["success"] is True
            record_id = saved["data"]["saved_job_id"]
            request_json("POST", "/api/saved-jobs", payload=forged)
            rows = mysql.fetch_all("SELECT id,user_id,job_id,job_title,salary,match_score,"
                "required_skills,publication_id FROM saved_job WHERE user_id='favorite_owner'")
            assert len(rows) == 1
            assert rows[0]["id"] == record_id and rows[0]["user_id"] == "favorite_owner"
            assert rows[0]["publication_id"] == publication_id and rows[0]["job_id"] == name
            assert rows[0]["job_title"] == "数据分析实习生"
            assert rows[0]["salary"] is None and rows[0]["match_score"] is None
            stored_skills = rows[0]["required_skills"]
            stored_skills = json.loads(stored_skills) if isinstance(stored_skills, str) else stored_skills
            assert stored_skills == snapshot["required_skills"]

            status, listed = request_json("GET", "/api/saved-jobs")
            item = next(row for row in listed["data"] if row["saved_job_id"] == record_id)
            assert status == 200 and item["publication_status"] == "current"
            assert item["publication_version"] == "v1"
            assert item["publication_snapshot"]["position_name"] == "数据分析实习生"
            assert "user_id" not in item and "publication_id" not in item
            assert "content_sha256" not in item
            assert "reviewed_by" not in item["publication_snapshot"]
            status, foreign = request_json("GET", "/api/saved-jobs", owner="other_student")
            assert status == 200 and not foreign["data"]
            with pytest.raises(Exception) as foreign_delete:
                request_json("DELETE", f"/api/saved-jobs/records/{record_id}", owner="other_student")
            assert getattr(foreign_delete.value, "code", None) == 404
            assert mysql.fetch_all("SELECT id FROM saved_job WHERE id=:id", {"id": record_id})

            mysql.execute_write("UPDATE job_catalog_publication SET status='revoked' WHERE id=:id",
                                {"id": publication_id})
            status, historical = request_json("GET", "/api/saved-jobs")
            item = next(row for row in historical["data"] if row["saved_job_id"] == record_id)
            assert status == 200 and item["publication_status"] == "revoked"
            assert item["publication_snapshot"]["source_text"] == source
            with pytest.raises(Exception) as stale_save:
                request_json("POST", "/api/saved-jobs", payload={"publication_ref": ref})
            assert getattr(stale_save.value, "code", None) == 409
            assert mysql.fetch_all("SELECT id FROM saved_job WHERE user_id='favorite_owner'") == [{"id": record_id}]

            status, removed = request_json("DELETE", f"/api/saved-jobs/records/{record_id}")
            assert status == 200 and removed["success"] is True
            assert mysql.fetch_all("SELECT id FROM saved_job WHERE id=:id", {"id": record_id}) == []
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
    finally:
        if mysql is not None:
            mysql.close()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_MYSQL=1 for real-MySQL favorites test")
def test_saved_job_and_target_role_authoritative_http_round_trip(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setenv("CPA_DEV_AUTH", "1")
    suffix = uuid.uuid4().hex[:12]
    owner = f"accept_fav_{suffix}"
    other = f"accept_other_{suffix}"
    job_id = f"job_{suffix}"
    role_name = f"Role_{suffix}"
    mysql = get_mysql_client()
    if not mysql.fetch_all("SHOW COLUMNS FROM saved_job LIKE 'publication_id'"):
        pytest.skip("configured MySQL schema is older than 0013; use an upgraded disposable schema")
    class DevHeaderAcceptanceConfig(Config):
        # This test deliberately exercises the strict, loopback-only dev
        # identity; the developer's real account SECRET_KEY must not override it.
        SECRET_KEY = None
        DEMO_MODE = False

    app = create_app(DevHeaderAcceptanceConfig)
    assert not app.extensions["cpa_account_auth_enabled"]
    server = make_server("127.0.0.1", 0, app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    def request_json(method, path, student_id, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"X-Dev-Student-Id": student_id}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(base_url + path, data=body, headers=headers, method=method)
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)

    try:
        status, saved = request_json(
            "POST", "/api/saved-jobs", owner,
            {"job_id": job_id, "job_title": "Acceptance Job", "required_skills": ["Python"]},
        )
        assert status == 200 and saved["success"] is True
        rows = mysql.execute(
            "SELECT id, user_id FROM saved_job WHERE user_id=:owner AND job_id=:job",
            {"owner": owner, "job": job_id},
        )
        assert len(rows) == 1
        assert saved["data"]["saved_job_id"] == rows[0]["id"]
        assert rows[0]["user_id"] == owner

        status, own_jobs = request_json("GET", "/api/saved-jobs", owner)
        assert status == 200 and any(row["job_id"] == job_id for row in own_jobs["data"])
        status, foreign_jobs = request_json("GET", "/api/saved-jobs", other)
        assert status == 200 and not any(row["job_id"] == job_id for row in foreign_jobs["data"])

        status, removed = request_json("DELETE", f"/api/saved-jobs/{job_id}", owner)
        assert status == 200 and removed["success"] is True
        assert mysql.execute(
            "SELECT id FROM saved_job WHERE user_id=:owner AND job_id=:job",
            {"owner": owner, "job": job_id},
        ) == []

        status, added = request_json(
            "POST", "/api/target-roles", owner, {"role_name": role_name, "priority": 1},
        )
        assert status == 200 and added["success"] is True
        rows = mysql.execute(
            "SELECT id, user_id FROM target_role WHERE user_id=:owner AND role_name=:role",
            {"owner": owner, "role": role_name},
        )
        assert len(rows) == 1
        assert added["data"]["target_role_id"] == rows[0]["id"]
        assert rows[0]["user_id"] == owner

        status, own_roles = request_json("GET", "/api/target-roles", owner)
        assert status == 200 and any(row["role_name"] == role_name for row in own_roles["data"])
        status, foreign_roles = request_json("GET", "/api/target-roles", other)
        assert status == 200 and not any(row["role_name"] == role_name for row in foreign_roles["data"])

        status, removed = request_json("DELETE", f"/api/target-roles/{role_name}", owner)
        assert status == 200 and removed["success"] is True
        assert mysql.execute(
            "SELECT id FROM target_role WHERE user_id=:owner AND role_name=:role",
            {"owner": owner, "role": role_name},
        ) == []
    finally:
        mysql.execute_write(
            "DELETE FROM saved_job WHERE user_id=:owner AND job_id=:job",
            {"owner": owner, "job": job_id},
        )
        mysql.execute_write(
            "DELETE FROM target_role WHERE user_id=:owner AND role_name=:role",
            {"owner": owner, "role": role_name},
        )
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
