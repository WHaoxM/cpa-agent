"""Opt-in real HTTP account password change and all-device revocation."""

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


def test_authenticated_password_change_revokes_both_cookies_and_rejects_old_password():
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client

    nonce = uuid.uuid4().hex[:12]
    username = f"password_student_{nonce}"
    old_password = "old-strong-account-password-42"
    new_password = "new-strong-account-password-43"
    config = type("PasswordChangeConfig", (Config,), {
        "SECRET_KEY": "only-for-password-change-test-a-long-random-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"account-password-test-{nonce}",
    })
    app = create_app(config)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    browsers = [requests.Session(), requests.Session()]
    db = get_mysql_client()
    try:
        for browser in browsers:
            browser.trust_env = False
        first_csrf = browsers[0].get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        registration = browsers[0].post(
            f"{base}/api/auth/register",
            json={"username": username, "password": old_password},
            headers={"X-CSRFToken": first_csrf}, timeout=5,
        )
        assert registration.status_code == 201, registration.text
        account_id = registration.json()["user"]["id"]
        tokens = []
        for browser in browsers:
            token = browser.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
            tokens.append(token)
            login = browser.post(f"{base}/api/auth/login",
                                 json={"username": username, "password": old_password},
                                 headers={"X-CSRFToken": token}, timeout=5)
            assert login.status_code == 200, login.text
        before = db.execute_single(
            "SELECT auth_epoch,password_hash FROM user_account WHERE id=:id", {"id": account_id})

        no_csrf = browsers[0].post(
            f"{base}/api/auth/change-password",
            json={"current_password": old_password, "new_password": new_password},
            timeout=5,
        )
        assert no_csrf.status_code == 400
        wrong = browsers[0].post(
            f"{base}/api/auth/change-password",
            json={"current_password": "incorrect-password-99", "new_password": new_password},
            headers={"X-CSRFToken": tokens[0]}, timeout=5,
        )
        assert wrong.status_code == 401
        short = browsers[0].post(
            f"{base}/api/auth/change-password",
            json={"current_password": old_password, "new_password": "short"},
            headers={"X-CSRFToken": tokens[0]}, timeout=5,
        )
        assert short.status_code == 400
        unchanged = browsers[0].post(
            f"{base}/api/auth/change-password",
            json={"current_password": old_password, "new_password": old_password},
            headers={"X-CSRFToken": tokens[0]}, timeout=5,
        )
        assert unchanged.status_code == 400
        assert db.execute_single(
            "SELECT auth_epoch,password_hash FROM user_account WHERE id=:id",
            {"id": account_id}) == before

        changed = browsers[0].post(
            f"{base}/api/auth/change-password",
            json={"current_password": old_password, "new_password": new_password},
            headers={"X-CSRFToken": tokens[0]}, timeout=5,
        )
        assert changed.status_code == 200, changed.text
        assert changed.json() == {"success": True, "reauthentication_required": True}
        after = db.execute_single(
            "SELECT auth_epoch,password_hash FROM user_account WHERE id=:id",
            {"id": account_id})
        assert after["auth_epoch"] == before["auth_epoch"] + 1
        assert after["password_hash"] != before["password_hash"]
        assert new_password not in after["password_hash"]
        for browser in browsers:
            assert browser.get(f"{base}/api/auth/me", timeout=5).status_code == 401
            assert browser.get(f"{base}/api/profile/student/{account_id}/basic",
                               timeout=5).status_code == 401

        relogin_token = browsers[0].get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        assert browsers[0].post(f"{base}/api/auth/login",
                                json={"username": username, "password": old_password},
                                headers={"X-CSRFToken": relogin_token}, timeout=5).status_code == 401
        valid_login = browsers[0].post(
            f"{base}/api/auth/login",
            json={"username": username, "password": new_password},
            headers={"X-CSRFToken": relogin_token}, timeout=5,
        )
        assert valid_login.status_code == 200, valid_login.text
        assert browsers[0].get(f"{base}/api/auth/me", timeout=5).json()["user"]["id"] == account_id
    finally:
        for browser in browsers:
            browser.close()
        server.shutdown()
        thread.join(timeout=5)
        db.execute_write("DELETE FROM user_account WHERE username=:name", {"name": username})
        assert db.execute_single("SELECT COUNT(*) AS n FROM user_account WHERE username=:name",
                                 {"name": username})["n"] == 0
