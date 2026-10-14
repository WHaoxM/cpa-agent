"""Real account/socket/MySQL proof of one staged record -> reviewed publication."""

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires migrated MySQL and Redis account sessions",
)


def _candidate(nonce):
    source = ("Dataset test role. Required Python skill. Bachelor's degree required. "
              "No major restriction. No certificates required. Synthetic fixture, not a real job.")
    return {
        "position_name": "Dataset test role",
        "source_uri": f"https://example.test/dataset/{nonce}",
        "source_text": source,
        "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_fetched_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "source_closes_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "source_license": "synthetic-test-only",
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": "Dataset test role"},
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


def test_dataset_preview_and_publish_recheck_exact_staged_record(monkeypatch, tmp_path):
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client

    nonce = uuid.uuid4().hex[:12]
    username, student_name = f"ds_pub_admin_{nonce}", f"ds_pub_student_{nonce}"
    password = "strong-dataset-publication-test-password-42"
    job_id = f"ds_job_{nonce}"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    config = type("DatasetPublicationConfig", (Config,), {
        "SECRET_KEY": "dataset-publication-test-secret-12345678901234567890",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"dataset-publication-{nonce}",
    })
    app = create_app(config)
    db = get_mysql_client()
    provisioned = app.test_cli_runner().invoke(
        args=["provision-admin", "--username", username],
        input=f"{password}\n{password}\n")
    assert provisioned.exit_code == 0, provisioned.output
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    admin, student = requests.Session(), requests.Session()
    admin.trust_env = student.trust_env = False
    dataset_id, path, publication_id = None, None, None
    candidate = _candidate(nonce)
    raw = json.dumps([{"title": candidate["position_name"], "snapshot": candidate},
                      {"title": "other role"}], ensure_ascii=False).encode()
    dataset_digest = hashlib.sha256(raw).hexdigest()
    try:
        csrf = admin.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        assert admin.post(f"{base}/api/auth/login", json={"username": username,
                          "password": password}, headers={"X-CSRFToken": csrf},
                          timeout=5).status_code == 200
        csrf = admin.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        headers = {"X-CSRFToken": csrf}
        student_csrf = student.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        assert student.post(f"{base}/api/auth/register",
            json={"username": student_name, "password": password},
            headers={"X-CSRFToken": student_csrf}, timeout=5).status_code == 201
        assert student.post(f"{base}/api/auth/login",
            json={"username": student_name, "password": password},
            headers={"X-CSRFToken": student_csrf}, timeout=5).status_code == 200
        student_csrf = student.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]

        uploaded = admin.post(f"{base}/api/admin/dataset/upload",
            data={"title": "synthetic unverified source"},
            files={"file": ("jobs.json", raw, "application/json")},
            headers=headers, timeout=10)
        assert uploaded.status_code == 200, uploaded.text
        dataset_id = uploaded.json()["data"]["id"]
        assert uploaded.json()["data"]["content_sha256"] == dataset_digest
        row = db.execute_single("SELECT file_path,count,staged_record_count FROM job_dataset "
                                "WHERE id=:id", {"id": dataset_id})
        path = Path(row["file_path"])
        assert row["count"] == 0 and row["staged_record_count"] == 2

        source = {"source_dataset_id": dataset_id, "source_record_index": 0,
                  "expected_dataset_sha256": dataset_digest}
        payload = {"job_portrait_id": job_id, "publication_version": "v1", **source}
        assert student.post(f"{base}/api/admin/job-publications/preview", json=payload,
            headers={"X-CSRFToken": student_csrf, "X-Admin": "true"},
            timeout=5).status_code == 403
        assert admin.post(f"{base}/api/admin/job-publications/preview",
            json={**payload, "snapshot": candidate}, headers=headers,
            timeout=5).status_code == 400
        assert admin.post(f"{base}/api/admin/job-publications/preview",
            json={**payload, "expected_dataset_sha256": "0" * 64},
            headers=headers, timeout=5).status_code == 409
        assert admin.post(f"{base}/api/admin/job-publications/preview",
            json={**payload, "source_dataset_id": "kb_" + dataset_id[3:]},
            headers=headers, timeout=5).status_code == 400
        forged = {**candidate, "staged_dataset_id": dataset_id}
        assert admin.post(f"{base}/api/admin/job-publications/preview",
            json={"job_portrait_id": job_id, "publication_version": "forged",
                  "snapshot": forged}, headers=headers, timeout=5).status_code == 400

        preview = admin.post(f"{base}/api/admin/job-publications/preview",
            json=payload, headers=headers, timeout=5)
        assert preview.status_code == 200, preview.text
        approved = preview.json()
        record_digest = approved["dataset_reference"]["expected_record_sha256"]
        assert approved["snapshot_for_review"]["staged_dataset_id"] == dataset_id
        assert approved["snapshot_for_review"]["source_provenance_kind"] == "admin_staged_dataset"
        assert db.execute_single("SELECT id FROM job_catalog_publication "
                                 "WHERE job_portrait_id=:jid", {"jid": job_id}) is None

        publish = {**payload, "expected_record_sha256": record_digest,
                   "preview_token": approved["preview_token"],
                   "expected_candidate_sha256": approved["candidate_sha256"],
                   "expected_source_text_sha256": approved["source_text_sha256"],
                   "confirm_review": {"source_checked": True,
                                      "requirements_checked": True,
                                      "license_checked": True,
                                      "publish_exact_digest": True}}
        path.write_bytes(raw + b" ")
        unavailable = admin.post(f"{base}/api/admin/job-publications",
                                 json=publish, headers=headers, timeout=5)
        assert unavailable.status_code == 503, unavailable.text
        assert db.execute_single("SELECT id FROM job_catalog_publication "
                                 "WHERE job_portrait_id=:jid", {"jid": job_id}) is None
        path.write_bytes(raw)
        stale = admin.post(f"{base}/api/admin/job-publications",
            json={**publish, "expected_record_sha256": "0" * 64},
            headers=headers, timeout=5)
        assert stale.status_code == 409, stale.text
        assert db.execute_single("SELECT id FROM job_catalog_publication "
                                 "WHERE job_portrait_id=:jid", {"jid": job_id}) is None

        # The API checked a valid source, but it changed before the publish
        # transaction: the transactional second read must still abort.
        from app.services import admin_job_publication as publication_service
        original_resolve = publication_service.resolve_staged_candidate
        def mutate_before_transaction(conn, **kwargs):
            path.write_bytes(raw + b" ")
            return original_resolve(conn, **kwargs)
        monkeypatch.setattr(publication_service, "resolve_staged_candidate",
                            mutate_before_transaction)
        raced = admin.post(f"{base}/api/admin/job-publications",
                           json=publish, headers=headers, timeout=5)
        assert raced.status_code == 503, raced.text
        assert db.execute_single("SELECT id FROM job_catalog_publication "
                                 "WHERE job_portrait_id=:jid", {"jid": job_id}) is None
        monkeypatch.setattr(publication_service, "resolve_staged_candidate",
                            original_resolve)
        path.write_bytes(raw)

        confirmed = admin.post(f"{base}/api/admin/job-publications",
                               json=publish, headers=headers, timeout=5)
        assert confirmed.status_code == 201, confirmed.text
        publication_id = confirmed.json()["id"]
        row = db.execute_single("SELECT snapshot_json,content_sha256,status FROM "
                                "job_catalog_publication WHERE id=:id", {"id": publication_id})
        snapshot = json.loads(row["snapshot_json"]) if isinstance(row["snapshot_json"], str) else row["snapshot_json"]
        assert row["status"] == "published"
        assert snapshot["staged_dataset_id"] == dataset_id
        assert snapshot["staged_dataset_sha256"] == dataset_digest
        assert snapshot["staged_record_sha256"] == record_digest
        assert snapshot["review_state"] == "approved"
        assert db.execute_single("SELECT COUNT(*) AS n FROM admin_audit_log "
            "WHERE target_type='job_catalog_publication' AND target_id=:jid",
            {"jid": job_id})["n"] == 1
        audit = db.execute_single("SELECT detail_json FROM admin_audit_log "
            "WHERE target_type='job_catalog_publication' AND target_id=:jid "
            "ORDER BY id DESC LIMIT 1", {"jid": job_id})
        audit_detail = json.loads(audit["detail_json"]) if isinstance(audit["detail_json"], str) else audit["detail_json"]
        assert audit_detail["dataset_reference"]["dataset_id"] == dataset_id
        assert audit_detail["dataset_reference"]["expected_record_sha256"] == record_digest
        assert db.execute_single("SELECT count FROM job_dataset WHERE id=:id",
                                 {"id": dataset_id})["count"] == 0
        content = admin.get(f"{base}/api/admin/dataset/{dataset_id}/content", timeout=5)
        assert content.status_code == 200, content.text
        assert content.json()["data"]["published"] is True
        assert content.json()["data"]["publication_count"] == 1
        assert content.json()["data"]["active_publication_count"] == 1
        retained = admin.delete(f"{base}/api/admin/dataset/{dataset_id}",
                                headers=headers, timeout=5)
        assert retained.status_code == 409, retained.text
        assert path.read_bytes() == raw
        assert db.execute_single("SELECT id FROM job_dataset WHERE id=:id",
                                 {"id": dataset_id}) is not None
    finally:
        admin.close()
        student.close()
        server.shutdown()
        thread.join(timeout=5)
        db.execute_write("DELETE FROM admin_audit_log WHERE "
                         "target_type='job_catalog_publication' AND target_id=:jid",
                         {"jid": job_id})
        db.execute_write("DELETE FROM job_catalog_publication WHERE job_portrait_id=:jid",
                         {"jid": job_id})
        if dataset_id:
            db.execute_write("DELETE FROM job_dataset WHERE id=:id", {"id": dataset_id})
        if path and path.resolve().is_relative_to(tmp_path.resolve()):
            path.unlink(missing_ok=True)
        db.execute_write("DELETE FROM user_account WHERE username IN (:a,:s)",
                         {"a": username, "s": student_name})
