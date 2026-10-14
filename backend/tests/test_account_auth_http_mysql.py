"""Opt-in real MySQL + Redis HTTP contract for self-managed accounts."""

import os
import threading
import uuid

import pytest
import requests
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires Docker MySQL and Redis",
)


@pytest.fixture(autouse=True)
def migrated_account_session_epoch():
    """Opt-in Docker suite applies the same explicit additive migration as deployment."""
    from app.services.account_session_schema import (
        account_session_schema_ready,
        migrate_account_session_schema,
    )

    migrate_account_session_schema()
    migrate_account_session_schema()  # Replay must be harmless.
    assert account_session_schema_ready()


def test_account_registration_login_roles_revocation_and_csrf():
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client

    nonce = uuid.uuid4().hex[:12]
    student_name = f"test_student_{nonce}"
    admin_name = f"test_admin_{nonce}"
    student_ip = {"REMOTE_ADDR": f"2001:db8:1::{nonce}"}
    admin_ip = {"REMOTE_ADDR": f"2001:db8:2::{nonce}"}
    password = "a-strong-test-password-42"
    auth_config = type("LiveAccountAuthConfig", (Config,), {
        "SECRET_KEY": "only-for-live-test-a-long-randomly-shaped-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "CORS_ALLOWED_ORIGINS": ["https://app.example.test"],
        "RATELIMIT_KEY_PREFIX": f"account-test-{nonce}",
    })
    app = create_app(auth_config)
    student = app.test_client()
    admin = app.test_client()
    db = get_mysql_client()
    try:
        assert student.get("/api/auth/me").status_code == 401
        csrf = student.get("/api/auth/csrf").json["csrfToken"]
        body = {"username": student_name, "password": password,
                "role": "admin", "id": "student_001"}
        assert student.post("/api/auth/register", json=body,
                            environ_overrides=student_ip).status_code == 400
        assert db.execute_single(
            "SELECT id FROM user_account WHERE username=:name", {"name": student_name}
        ) is None
        registered = student.post(
            "/api/auth/register", json=body, headers={"X-CSRFToken": csrf},
            environ_overrides=student_ip,
        )
        assert registered.status_code == 201, registered.json
        account_id = registered.json["user"]["id"]
        row = db.execute_single(
            "SELECT id, role, status, password_hash FROM user_account WHERE username=:name",
            {"name": student_name},
        )
        assert row["id"] == account_id and row["id"] != "student_001"
        assert row["role"] == "student" and row["status"] == "active"
        assert row["password_hash"].startswith("$argon2id$")
        assert student.post("/api/auth/login", json=body,
                            headers={"X-CSRFToken": "wrong"},
                            environ_overrides=student_ip).status_code == 400
        assert student.post("/api/auth/login", json={"username": student_name,
                            "password": "wrong-test-password-44"},
                            headers={"X-CSRFToken": csrf},
                            environ_overrides=student_ip).status_code == 401
        logged_in = student.post("/api/auth/login", json=body,
                                 headers={"X-CSRFToken": csrf},
                                 environ_overrides=student_ip)
        assert logged_in.status_code == 200, logged_in.json
        assert student.get("/api/auth/me").json["user"]["id"] == account_id
        assert student.get(f"/api/profile/student/{account_id}/basic").status_code == 404
        assert student.get("/api/profile/student/student_001/basic").status_code == 403
        assert student.get("/api/admin/python-info",
                           headers={"X-Admin": "true"}).status_code == 403
        old_cookie = student.get_cookie("cpa_session")
        assert old_cookie is not None

        # CLI path is the only route that can create an admin.
        runner = app.test_cli_runner()
        provisioned = runner.invoke(args=["provision-admin", "--username", admin_name],
                                    input=f"{password}\n{password}\n")
        assert provisioned.exit_code == 0, provisioned.output
        admin_csrf = admin.get("/api/auth/csrf").json["csrfToken"]
        assert admin.post("/api/auth/login", json={"username": admin_name,
                "password": password}, headers={"X-CSRFToken": admin_csrf},
                environ_overrides=admin_ip).status_code == 200
        assert admin.get("/api/admin/python-info").status_code == 200
        admin_id = db.execute_single(
            "SELECT id FROM user_account WHERE username=:name", {"name": admin_name}
        )["id"]
        db.execute_write("UPDATE user_account SET role='student' WHERE id=:id",
                         {"id": admin_id})
        assert admin.get("/api/admin/python-info").status_code == 401
        db.execute_write("UPDATE user_account SET role='admin' WHERE id=:id",
                         {"id": admin_id})
        assert admin.get("/api/admin/python-info").status_code == 401
        admin_csrf = admin.get("/api/auth/csrf").json["csrfToken"]
        assert admin.post("/api/auth/login", json={"username": admin_name,
                "password": password}, headers={"X-CSRFToken": admin_csrf},
                environ_overrides=admin_ip).status_code == 200
        assert admin.get("/api/admin/python-info").status_code == 200

        # Database status takes effect on the already-issued cookie.
        db.execute_write("UPDATE user_account SET status='disabled' WHERE id=:id",
                         {"id": account_id})
        assert student.get("/api/auth/me").status_code == 401
        db.execute_write("UPDATE user_account SET status='active' WHERE id=:id",
                         {"id": account_id})
        revoked = app.test_client()
        revoked.set_cookie("cpa_session", old_cookie.value)
        assert revoked.get("/api/auth/me").status_code == 401
        fresh_csrf = student.get("/api/auth/csrf").json["csrfToken"]
        assert student.post("/api/auth/login", json=body,
                            headers={"X-CSRFToken": fresh_csrf},
                            environ_overrides=student_ip).status_code == 200
        logout_cookie = student.get_cookie("cpa_session")
        assert student.post("/api/auth/logout").status_code == 400
        logout_csrf = student.get("/api/auth/csrf").json["csrfToken"]
        assert student.post("/api/auth/logout", headers={"X-CSRFToken": logout_csrf}).status_code == 200
        replay = app.test_client()
        replay.set_cookie("cpa_session", logout_cookie.value)
        assert replay.get("/api/auth/me").status_code == 401

        # Wildcard never becomes credentialed; explicit origins may.
        allowed = admin.get("/api/auth/me", headers={"Origin": "https://app.example.test"})
        assert allowed.headers["Access-Control-Allow-Credentials"] == "true"
        denied = admin.get("/api/auth/me", headers={"Origin": "https://evil.example.test"})
        assert "Access-Control-Allow-Origin" not in denied.headers
    finally:
        db.execute_write("DELETE FROM user_account WHERE username IN (:student,:admin)",
                         {"student": student_name, "admin": admin_name})


def test_login_bad_password_is_redis_rate_limited():
    from app import create_app
    from app.config import Config

    auth_config = type("RateLimitConfig", (Config,), {
        "SECRET_KEY": "only-for-rate-test-a-long-randomly-shaped-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"account-test-{uuid.uuid4().hex}",
    })
    app = create_app(auth_config)
    client = app.test_client()
    csrf = client.get("/api/auth/csrf").json["csrfToken"]
    ip = f"2001:db8:3::{uuid.uuid4().hex[:12]}"
    for _ in range(5):
        response = client.post(
            "/api/auth/login", json={"username": "nonexistent_user",
                                       "password": "not-a-valid-password-42"},
            headers={"X-CSRFToken": csrf},
            environ_overrides={"REMOTE_ADDR": ip},
        )
        assert response.status_code == 401, response.json
    limited = client.post(
        "/api/auth/login", json={"username": "nonexistent_user",
                                   "password": "not-a-valid-password-42"},
        headers={"X-CSRFToken": csrf},
        environ_overrides={"REMOTE_ADDR": ip},
    )
    assert limited.status_code == 429
    assert limited.json["code"] == "RATE_LIMITED"


def test_account_cookie_round_trip_over_real_local_http_socket():
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client

    nonce = uuid.uuid4().hex[:12]
    username = f"socket_student_{nonce}"
    password = "a-strong-socket-test-password-42"
    auth_config = type("SocketAccountConfig", (Config,), {
        "SECRET_KEY": "only-for-socket-test-a-long-randomly-shaped-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"account-test-{nonce}",
    })
    app = create_app(auth_config)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    browser = requests.Session()
    try:
        token = browser.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        body = {"username": username, "password": password}
        registered = browser.post(f"{base}/api/auth/register", json=body,
                                  headers={"X-CSRFToken": token}, timeout=5)
        assert registered.status_code == 201, registered.text
        account_id = registered.json()["user"]["id"]
        assert browser.post(f"{base}/api/auth/login", json=body,
                            headers={"X-CSRFToken": token}, timeout=5).status_code == 200
        assert browser.get(f"{base}/api/auth/me", timeout=5).json()["user"]["id"] == account_id
        assert browser.get(f"{base}/api/profile/student/student_001/basic",
                           timeout=5).status_code == 403
        assert browser.post(f"{base}/api/auth/logout",
                            headers={"X-CSRFToken": token}, timeout=5).status_code == 200
        assert browser.get(f"{base}/api/auth/me", timeout=5).status_code == 401
    finally:
        browser.close()
        server.shutdown()
        thread.join(timeout=5)
        get_mysql_client().execute_write(
            "DELETE FROM user_account WHERE username=:name", {"name": username}
        )


def test_dev_header_cannot_bypass_configured_account_login(monkeypatch):
    from app import create_app
    from app.config import Config

    monkeypatch.setenv("CPA_DEV_AUTH", "1")
    auth_config = type("NoDevBypassAuthConfig", (Config,), {
        "SECRET_KEY": "only-for-dev-bypass-test-a-long-random-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
    })
    client = create_app(auth_config).test_client()
    forged = {"X-Dev-Student-Id": "student_001", "X-Admin": "true"}
    assert client.get("/api/profile/student/student_001/basic",
                      headers=forged).status_code == 401
    assert client.get("/api/admin/python-info", headers=forged).status_code == 401


def test_two_idle_cookies_cannot_resurrect_after_account_reenabled():
    """Neither browser observes disabled; re-enable must still revoke both."""
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client

    nonce = uuid.uuid4().hex[:12]
    username = f"revoke_student_{nonce}"
    password = "a-strong-revocation-test-password-42"
    config = type("RevocationConfig", (Config,), {
        "SECRET_KEY": "only-for-revocation-test-a-long-random-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"account-revoke-test-{nonce}",
    })
    app = create_app(config)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    browsers = [requests.Session(), requests.Session()]
    db = get_mysql_client()
    try:
        body = {"username": username, "password": password}
        for browser in browsers:
            browser.trust_env = False
        csrf = browsers[0].get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        registered = browsers[0].post(f"{base}/api/auth/register", json=body,
                                      headers={"X-CSRFToken": csrf}, timeout=5)
        assert registered.status_code == 201, registered.text
        account_id = registered.json()["user"]["id"]
        for browser in browsers:
            token = browser.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
            response = browser.post(f"{base}/api/auth/login", json=body,
                                    headers={"X-CSRFToken": token}, timeout=5)
            assert response.status_code == 200, response.text
            assert browser.cookies.get("cpa_session")

        before_epoch = db.execute_single(
            "SELECT auth_epoch FROM user_account WHERE id=:id", {"id": account_id}
        )["auth_epoch"]
        db.execute_write("UPDATE user_account SET name='No revocation' WHERE id=:id",
                         {"id": account_id})
        unchanged_epoch = db.execute_single(
            "SELECT auth_epoch FROM user_account WHERE id=:id", {"id": account_id}
        )["auth_epoch"]
        assert unchanged_epoch == before_epoch
        db.execute_write("UPDATE user_account SET status='disabled' WHERE id=:id",
                         {"id": account_id})
        db.execute_write("UPDATE user_account SET status='active' WHERE id=:id",
                         {"id": account_id})
        after_epoch = db.execute_single(
            "SELECT auth_epoch FROM user_account WHERE id=:id", {"id": account_id}
        )["auth_epoch"]
        assert after_epoch == before_epoch + 2
        for browser in browsers:
            assert browser.get(f"{base}/api/auth/me", timeout=5).status_code == 401
            assert browser.get(f"{base}/api/profile/student/{account_id}/basic",
                               timeout=5).status_code == 401
    finally:
        for browser in browsers:
            browser.close()
        server.shutdown()
        thread.join(timeout=5)
        db.execute_write("DELETE FROM user_account WHERE username=:name",
                         {"name": username})


def test_unmigrated_account_schema_fails_closed_without_dev_header(monkeypatch):
    from app import create_app
    from app.config import Config
    from app.services import account_session_schema

    monkeypatch.setenv("CPA_DEV_AUTH", "1")
    monkeypatch.setattr(account_session_schema, "account_session_schema_ready",
                        lambda: False)
    config = type("UnmigratedAccountConfig", (Config,), {
        "SECRET_KEY": "only-for-unmigrated-test-a-long-random-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
    })
    app = create_app(config)
    assert app.extensions["cpa_account_auth_enabled"] is True
    assert app.extensions["cpa_account_session_schema_ready"] is False
    client = app.test_client()
    assert client.get("/api/auth/csrf").status_code == 503
    assert client.get("/api/profile/student/student_001/basic",
                      headers={"X-Dev-Student-Id": "student_001"}).status_code == 401
