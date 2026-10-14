"""Opt-in real account, Redis session and MySQL publication HTTP contract."""

import hashlib
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires Docker MySQL and Redis",
)


def _candidate(nonce):
    source = ("Test-only role. Required Python skill. Bachelor's degree required. "
              "No major restriction. No certificates required. Synthetic fixture, not a real job.")
    return {
        "position_name": "Test-only role",
        "source_uri": f"https://example.test/jobs/{nonce}",
        "source_text": source,
        "source_text_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "source_fetched_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "source_closes_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "source_license": "test-fixture",
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": "Test-only role"},
            "degree_requirement": {"status": "explicit", "excerpt": "Bachelor's degree required"},
            "major_requirements": {"status": "explicit", "excerpt": "No major restriction"},
            "major_strict": {"status": "explicit", "excerpt": "No major restriction"},
            "required_certs": {"status": "explicit", "excerpt": "No certificates required"},
            "required_skills": {"status": "explicit", "excerpt": "Required Python skill"},
        },
        "required_skills": [{"name": "Python", "level": "必须"}],
        "job_profile": {"degree_requirement": "本科", "major_requirements": [],
                        "major_strict": False, "required_certs": []},
    }


def test_admin_preview_publish_revoke_uses_db_identity_and_never_falls_back(monkeypatch):
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client
    from app.services.job_publication import read_published_job

    nonce = uuid.uuid4().hex[:12]
    username = f"job_admin_{nonce}"
    job_id = f"test_job_{nonce}"
    password = "a-strong-test-password-42"
    auth_config = type("JobPublicationAuthConfig", (Config,), {
        "SECRET_KEY": "only-for-job-publication-test-a-long-random-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"job-publication-test-{nonce}",
    })
    app = create_app(auth_config)
    db = get_mysql_client()
    browser = app.test_client()
    candidate = _candidate(nonce)
    base = "/api/admin/job-publications"
    try:
        # A spoofed header is not a reviewer account.
        assert browser.post(base + "/preview", json={"job_portrait_id": job_id,
            "publication_version": "v1", "snapshot": candidate},
            headers={"X-Admin": "true"}).status_code == 401

        runner = app.test_cli_runner()
        provisioned = runner.invoke(args=["provision-admin", "--username", username],
                                    input=f"{password}\n{password}\n")
        assert provisioned.exit_code == 0, provisioned.output
        admin_id = db.execute_single("SELECT id FROM user_account WHERE username=:u",
                                     {"u": username})["id"]
        csrf = browser.get("/api/auth/csrf").json["csrfToken"]
        logged = browser.post("/api/auth/login", json={"username": username,
                              "password": password}, headers={"X-CSRFToken": csrf})
        assert logged.status_code == 200, logged.json
        headers = {"X-CSRFToken": csrf, "X-Admin": "false"}

        def preview(version, snapshot):
            return browser.post(base + "/preview", json={"job_portrait_id": job_id,
                "publication_version": version, "snapshot": snapshot}, headers=headers)

        def publish(version, snapshot, approved):
            return browser.post(base, json={"job_portrait_id": job_id,
                "publication_version": version, "snapshot": snapshot,
                "preview_token": approved["preview_token"],
                "expected_candidate_sha256": approved["candidate_sha256"],
                "expected_source_text_sha256": approved["source_text_sha256"],
                "confirm_review": {"source_checked": True,
                                   "requirements_checked": True,
                                   "license_checked": True,
                                   "publish_exact_digest": True}}, headers=headers)

        invalid = dict(candidate)
        invalid["reviewed_by"] = "forged-reviewer"
        assert preview("v1", invalid).status_code == 400
        not_ready = dict(candidate)
        not_ready["required_skills"] = []
        assert preview("v1", not_ready).status_code == 422
        expired = dict(candidate)
        expired["source_closes_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        assert preview("v1", expired).status_code == 400

        first = preview("v1", candidate)
        assert first.status_code == 200, first.json
        assert db.execute_single("SELECT id FROM job_catalog_publication WHERE job_portrait_id=:jid",
                                 {"jid": job_id}) is None
        changed = dict(candidate)
        changed["source_license"] = "changed-after-preview"
        denied = publish("v1", changed, first.json)
        assert denied.status_code == 409, denied.json
        assert db.execute_single("SELECT id FROM job_catalog_publication WHERE job_portrait_id=:jid",
                                 {"jid": job_id}) is None
        published = publish("v1", candidate, first.json)
        assert published.status_code == 201, published.json
        one = read_published_job(db, job_id)
        assert one["id"] == published.json["id"]
        assert one["snapshot_json"]["reviewed_by"] == admin_id
        assert one["snapshot_json"]["review_state"] == "approved"
        assert one["snapshot_json"]["source_text_sha256"] == candidate["source_text_sha256"]
        detail = browser.get(f"{base}/{one['id']}")
        assert detail.status_code == 200
        assert detail.json["snapshot"]["reviewed_by"] == admin_id
        listing = browser.get(base + "?limit=1&offset=0")
        assert listing.status_code == 200
        assert any(row["id"] == one["id"] for row in listing.json["items"])
        assert db.execute_single("SELECT action,admin_id,detail_json FROM admin_audit_log "
                                 "WHERE target_type='job_catalog_publication' AND target_id=:jid "
                                 "ORDER BY id DESC LIMIT 1", {"jid": job_id})["action"] == "publish"
        assert publish("v1", candidate, first.json).status_code == 409

        # If audit fails after INSERT, the published row must roll back too.
        from app.services import admin_job_publication as publication_service
        original_audit = publication_service._audit
        def fail_audit(*args, **kwargs):
            raise RuntimeError("injected audit failure")
        monkeypatch.setattr(publication_service, "_audit", fail_audit)
        uncommitted_preview = preview("audit-fails", candidate)
        assert uncommitted_preview.status_code == 200
        failed = publish("audit-fails", candidate, uncommitted_preview.json)
        assert failed.status_code == 500
        assert db.execute_single("SELECT id FROM job_catalog_publication WHERE job_portrait_id=:jid "
                                 "AND publication_version='audit-fails'", {"jid": job_id}) is None
        monkeypatch.setattr(publication_service, "_audit", original_audit)

        second = preview("v2", candidate)
        assert second.status_code == 200, second.json
        published_v2 = publish("v2", candidate, second.json)
        assert published_v2.status_code == 201, published_v2.json
        assert read_published_job(db, job_id)["id"] == published_v2.json["id"]
        bad_revoke = browser.post(f"{base}/{published_v2.json['id']}/revoke",
            json={"expected_content_sha256": "0" * 64, "reason": "test correction"},
            headers=headers)
        assert bad_revoke.status_code == 409
        revoked = browser.post(f"{base}/{published_v2.json['id']}/revoke",
            json={"expected_content_sha256": published_v2.json["content_sha256"],
                  "reason": "test correction"}, headers=headers)
        assert revoked.status_code == 200, revoked.json
        assert read_published_job(db, job_id) is None
        assert read_published_job(db, job_id, "v1") is not None
        revoked_detail = browser.get(f"{base}/{published_v2.json['id']}")
        assert revoked_detail.status_code == 200
        assert revoked_detail.json["status"] == "revoked"
        assert browser.post(f"{base}/{published_v2.json['id']}/revoke",
            json={"expected_content_sha256": published_v2.json["content_sha256"],
                  "reason": "test correction"}, headers=headers).status_code == 409
        actions = db.execute("SELECT action,admin_id FROM admin_audit_log "
                             "WHERE target_type='job_catalog_publication' AND target_id=:jid "
                             "ORDER BY id", {"jid": job_id})
        assert [row["action"] for row in actions] == ["publish", "publish", "revoke"]
        assert {row["admin_id"] for row in actions} == {admin_id}
    finally:
        db.execute_write("DELETE FROM admin_audit_log WHERE target_type='job_catalog_publication' "
                         "AND target_id=:jid", {"jid": job_id})
        db.execute_write("DELETE FROM job_catalog_publication WHERE job_portrait_id=:jid",
                         {"jid": job_id})
        db.execute_write("DELETE FROM user_account WHERE username=:u", {"u": username})


def test_student_account_cannot_preview_and_admin_socket_roundtrip():
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client

    nonce = uuid.uuid4().hex[:12]
    admin_name, student_name = f"job_admin_{nonce}", f"job_student_{nonce}"
    jid = f"socket_job_{nonce}"
    password = "a-strong-test-password-42"
    auth_config = type("SocketJobPublicationAuthConfig", (Config,), {
        "SECRET_KEY": "only-for-job-socket-test-a-long-random-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"job-socket-test-{nonce}",
    })
    app = create_app(auth_config)
    db = get_mysql_client()
    runner = app.test_cli_runner()
    provisioned = runner.invoke(args=["provision-admin", "--username", admin_name],
                                input=f"{password}\n{password}\n")
    assert provisioned.exit_code == 0, provisioned.output
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    student, admin = requests.Session(), requests.Session()
    snapshot = _candidate(nonce)
    try:
        student_csrf = student.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        registered = student.post(f"{base}/api/auth/register",
            json={"username": student_name, "password": password, "role": "admin"},
            headers={"X-CSRFToken": student_csrf}, timeout=5)
        assert registered.status_code == 201, registered.text
        assert student.post(f"{base}/api/auth/login",
            json={"username": student_name, "password": password},
            headers={"X-CSRFToken": student_csrf}, timeout=5).status_code == 200
        payload = {"job_portrait_id": jid, "publication_version": "v1", "snapshot": snapshot}
        denied = student.post(f"{base}/api/admin/job-publications/preview", json=payload,
                              headers={"X-CSRFToken": student_csrf, "X-Admin": "true"}, timeout=5)
        assert denied.status_code == 403, denied.text
        assert student.get(f"{base}/api/admin/job-publications", timeout=5).status_code == 403

        admin_csrf = admin.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        assert admin.post(f"{base}/api/auth/login",
            json={"username": admin_name, "password": password},
            headers={"X-CSRFToken": admin_csrf}, timeout=5).status_code == 200
        headers = {"X-CSRFToken": admin_csrf}
        preview = admin.post(f"{base}/api/admin/job-publications/preview",
                             json=payload, headers=headers, timeout=5)
        assert preview.status_code == 200, preview.text
        approved = preview.json()
        publication = admin.post(f"{base}/api/admin/job-publications", json={**payload,
            "preview_token": approved["preview_token"],
            "expected_candidate_sha256": approved["candidate_sha256"],
            "expected_source_text_sha256": approved["source_text_sha256"],
            "confirm_review": {"source_checked": True, "requirements_checked": True,
                               "license_checked": True, "publish_exact_digest": True}},
            headers=headers, timeout=5)
        assert publication.status_code == 201, publication.text
        publication_id = publication.json()["id"]
        row = db.execute_single("SELECT snapshot_json,content_sha256,status "
                                "FROM job_catalog_publication WHERE id=:id", {"id": publication_id})
        assert row["status"] == "published"
        assert row["content_sha256"] == publication.json()["content_sha256"]
        revoked = admin.post(f"{base}/api/admin/job-publications/{publication_id}/revoke",
            json={"expected_content_sha256": row["content_sha256"], "reason": "test only"},
            headers=headers, timeout=5)
        assert revoked.status_code == 200, revoked.text
        assert db.execute_single("SELECT status FROM job_catalog_publication WHERE id=:id",
                                 {"id": publication_id})["status"] == "revoked"
    finally:
        student.close()
        admin.close()
        server.shutdown()
        thread.join(timeout=5)
        db.execute_write("DELETE FROM admin_audit_log WHERE target_type='job_catalog_publication' "
                         "AND target_id=:jid", {"jid": jid})
        db.execute_write("DELETE FROM job_catalog_publication WHERE job_portrait_id=:jid",
                         {"jid": jid})
        db.execute_write("DELETE FROM user_account WHERE username IN (:a,:s)",
                         {"a": admin_name, "s": student_name})
