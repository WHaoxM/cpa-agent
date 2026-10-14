"""Opt-in real administrator account -> multipart staging -> owner audit read."""

import hashlib
import os
import threading
import uuid
from pathlib import Path

import pytest
import requests
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires migrated MySQL, Redis and platform account sessions",
)


def test_self_managed_admin_account_stages_content_without_publishing(monkeypatch, tmp_path):
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client

    nonce = uuid.uuid4().hex[:12]
    username = f"stage_admin_{nonce}"
    student_name = f"stage_student_{nonce}"
    password = "strong-stage-account-password-42"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    config = type("AdminStageAccountConfig", (Config,), {
        "SECRET_KEY": "stage-account-test-secret-12345678901234567890",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"stage-account-{nonce}",
    })
    app = create_app(config)
    provisioned = app.test_cli_runner().invoke(
        args=["provision-admin", "--username", username],
        input=f"{password}\n{password}\n")
    assert provisioned.exit_code == 0, provisioned.output
    db = get_mysql_client()
    admin_id = db.execute_single(
        "SELECT id FROM user_account WHERE username=:name", {"name": username})["id"]
    before_publications = db.execute_single(
        "SELECT COUNT(*) AS n FROM job_catalog_publication")["n"]
    before_postings = db.execute_single("SELECT COUNT(*) AS n FROM job_posting")["n"]

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    admin, student = requests.Session(), requests.Session()
    admin.trust_env = student.trust_env = False
    source_id = None
    path = None
    try:
        anon = admin.post(f"{base}/api/admin/dataset/upload",
                          data={"title": "No account"},
                          files={"file": ("jobs.json", b'[{"title":"Role"}]')}, timeout=10)
        assert anon.status_code == 401

        csrf = admin.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        signed_in = admin.post(f"{base}/api/auth/login",
                               json={"username": username, "password": password},
                               headers={"X-CSRFToken": csrf}, timeout=5)
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["user"]["role"] == "admin"
        csrf = admin.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]

        student_csrf = student.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        registered = student.post(f"{base}/api/auth/register",
                                  json={"username": student_name, "password": password},
                                  headers={"X-CSRFToken": student_csrf}, timeout=5)
        assert registered.status_code == 201, registered.text
        assert student.post(f"{base}/api/auth/login",
                            json={"username": student_name, "password": password},
                            headers={"X-CSRFToken": student_csrf}, timeout=5).status_code == 200
        student_csrf = student.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        denied = student.post(f"{base}/api/admin/dataset/upload",
                              data={"title": "No admin role"},
                              files={"file": ("jobs.json", b'[{"title":"Role"}]')},
                              headers={"X-CSRFToken": student_csrf}, timeout=10)
        assert denied.status_code == 403

        raw = '[{"title":"职位一"},{"title":"职位二"}]'.encode("utf-8")
        uploaded = admin.post(f"{base}/api/admin/dataset/upload",
                              data={"title": "仅暂存的岗位来源"},
                              files={"file": ("jobs.json", raw, "application/json")},
                              headers={"X-CSRFToken": csrf}, timeout=10)
        assert uploaded.status_code == 200, uploaded.text
        item = uploaded.json()["data"]
        source_id = item["id"]
        assert item["content_staged"] is True and item["content_ingested"] is False
        assert item["staged_record_count"] == 2 and item["count"] == 0
        assert item["content_sha256"] == hashlib.sha256(raw).hexdigest()
        row = db.execute_single("SELECT uploaded_by,count,staged_record_count,file_path "
                                "FROM job_dataset WHERE id=:id", {"id": source_id})
        assert row["uploaded_by"] == admin_id
        assert row["count"] == 0 and row["staged_record_count"] == 2
        path = Path(row["file_path"])
        assert path.read_bytes() == raw
        assert student.get(f"{base}/api/admin/dataset/{source_id}/content",
                           timeout=5).status_code == 403
        fetched = admin.get(f"{base}/api/admin/dataset/{source_id}/content", timeout=5)
        assert fetched.status_code == 200, fetched.text
        assert [x["title"] for x in fetched.json()["data"]["content"]] == ["职位一", "职位二"]
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_catalog_publication")["n"] == before_publications
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_posting")["n"] == before_postings

        removed = admin.delete(f"{base}/api/admin/dataset/{source_id}",
                               headers={"X-CSRFToken": csrf}, timeout=5)
        assert removed.status_code == 200, removed.text
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_dataset WHERE id=:id",
                                 {"id": source_id})["n"] == 0
        assert not path.exists()
        source_id = None
    finally:
        admin.close()
        student.close()
        server.shutdown()
        thread.join(timeout=5)
        if source_id:
            db.execute_write("DELETE FROM job_dataset WHERE id=:id", {"id": source_id})
        if path and path.resolve().is_relative_to(tmp_path.resolve()):
            path.unlink(missing_ok=True)
        db.execute_write("DELETE FROM user_account WHERE username IN (:admin,:student)",
                         {"admin": username, "student": student_name})
