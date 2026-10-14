"""A real account cookie reaches an owner-scoped business write over HTTP.

Opt-in: CPA_TEST_LIVE_MYSQL=1. Random account and saved-job IDs are cleaned.
"""

import os
import threading
import uuid

import pytest
import requests
from werkzeug.serving import make_server


@pytest.mark.skipif(os.environ.get("CPA_TEST_LIVE_MYSQL") != "1", reason="requires MySQL and Redis")
def test_registered_account_can_write_only_its_own_saved_job_over_http(monkeypatch):
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    nonce = uuid.uuid4().hex[:16]
    names = (f"biz_a_{nonce}", f"biz_b_{nonce}")
    job_id = f"biz_job_{nonce}"
    password = "a-strong-test-password-42"
    settings = type("BusinessWriteAccountConfig", (Config,), {
        "SECRET_KEY": "business-write-test-secret-with-at-least-32-characters",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"business-write-{nonce}",
    })
    app = create_app(settings)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    db = get_mysql_client()
    browsers = [requests.Session(), requests.Session()]
    tokens = []
    try:
        accounts = []
        for browser, username in zip(browsers, names):
            token = browser.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
            tokens.append(token)
            credentials = {"username": username, "password": password,
                           "role": "admin", "id": "student_001"}
            registered = browser.post(f"{base}/api/auth/register", json=credentials,
                                      headers={"X-CSRFToken": token}, timeout=5)
            assert registered.status_code == 201, registered.text
            account_id = registered.json()["user"]["id"]
            assert account_id != "student_001"
            assert browser.post(f"{base}/api/auth/login", json=credentials,
                                headers={"X-CSRFToken": token}, timeout=5).status_code == 200
            accounts.append((account_id, token))

        owner_id, owner_csrf = accounts[0]
        other_id, _ = accounts[1]
        payload = {"job_id": job_id, "job_title": "User bookmark only",
                   "user_id": other_id, "student_id": other_id}
        owner = browsers[0]
        denied = owner.post(f"{base}/api/saved-jobs", json=payload, timeout=5)
        assert denied.status_code == 400
        assert db.fetch_all("SELECT id FROM saved_job WHERE job_id=:job", {"job": job_id}) == []

        saved = owner.post(f"{base}/api/saved-jobs", json=payload,
                           headers={"X-CSRFToken": owner_csrf}, timeout=5)
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["source_verified"] is False
        rows = db.fetch_all("SELECT id,user_id FROM saved_job WHERE job_id=:job", {"job": job_id})
        assert len(rows) == 1 and rows[0]["user_id"] == owner_id
        assert saved.json()["data"]["saved_job_id"] == rows[0]["id"]

        own_list = owner.get(f"{base}/api/saved-jobs", timeout=5)
        other_list = browsers[1].get(f"{base}/api/saved-jobs", timeout=5)
        assert any(row["job_id"] == job_id for row in own_list.json()["data"])
        assert not any(row["job_id"] == job_id for row in other_list.json()["data"])
        removed = owner.delete(f"{base}/api/saved-jobs/{job_id}",
                               headers={"X-CSRFToken": owner_csrf}, timeout=5)
        assert removed.status_code == 200, removed.text
        assert db.fetch_all("SELECT id FROM saved_job WHERE job_id=:job", {"job": job_id}) == []

        assert owner.post(f"{base}/api/auth/logout", headers={"X-CSRFToken": owner_csrf},
                          timeout=5).status_code == 200
        assert owner.get(f"{base}/api/saved-jobs", timeout=5).status_code == 401
    finally:
        for browser, token in zip(browsers, tokens):
            try:
                browser.post(f"{base}/api/auth/logout", headers={"X-CSRFToken": token}, timeout=5)
            except requests.RequestException:
                pass
        for browser in browsers:
            browser.close()
        server.shutdown()
        thread.join(timeout=5)
        db.execute_write("DELETE FROM saved_job WHERE job_id=:job", {"job": job_id})
        db.execute_write("DELETE FROM user_account WHERE username IN (:a,:b)",
                         {"a": names[0], "b": names[1]})
