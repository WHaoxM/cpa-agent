"""Opt-in logout fencing through real HTTP, disposable MySQL and empty Redis."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

import pytest
import requests


pytestmark = pytest.mark.skipif(
    os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
    reason="explicit opt-in: disposable MySQL and dedicated empty Redis required",
)


@pytest.fixture(scope="module")
def logout_runtime():
    import pymysql
    import redis
    from argon2 import PasswordHasher
    from flask import request, session
    from werkzeug.serving import make_server
    from app import create_app
    from app.config import Config
    from app.services.db import mysql_client as mysql_module
    from app.services.db.mysql_client import MySQLClient

    nonce = uuid.uuid4().hex[:12]
    database = "cpa_logout_test_" + nonce
    redis_url = os.getenv("CPA_FRONTEND_REPLAY_REDIS_URL", "redis://127.0.0.1:16379/15")
    cache = redis.Redis.from_url(redis_url)
    assert cache.dbsize() == 0, "Refusing to touch a nonempty dedicated Redis DB"
    admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            autocommit=True)
    mysql = server = thread = None
    patch = pytest.MonkeyPatch()
    created = False
    gate = {"entered": threading.Event(), "release": threading.Event(), "modified": False}
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        created = True
        subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
                        "-x", f"database={database}", "upgrade", "head"],
                       cwd=Path(__file__).resolve().parents[1], check=True,
                       capture_output=True, text=True)
        mysql = MySQLClient(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            database=database, pool_size=5, max_overflow=0)
        patch.setattr(mysql_module, "_mysql_client", mysql)
        patch.setattr(Config, "DEMO_MODE", False)
        patch.setenv("CPA_DEV_AUTH", "0")
        password = "Disposable-logout-password-2026!"
        accounts = {}
        hashed = PasswordHasher().hash(password)
        for role in ("student", "admin"):
            owner = "acct_" + uuid.uuid4().hex
            username = f"logout_{role}_{nonce}"
            mysql.execute_write("INSERT INTO user_account(id,username,password_hash,role,status) "
                                "VALUES (:id,:username,:hash,:role,'active')",
                                {"id": owner, "username": username, "hash": hashed, "role": role})
            accounts[role] = {"id": owner, "username": username, "password": password}
        config = type("LogoutRaceConfig", (Config,), {
            "SECRET_KEY": "only-for-logout-race-a-long-random-secret-2026",
            "DEMO_MODE": False, "CPA_AUTH_COOKIE_SECURE": False,
            "MYSQL_DATABASE": database, "REDIS_URL": redis_url,
            "RATELIMIT_KEY_PREFIX": "logout-" + nonce,
        })
        app = create_app(config)
        assert app.extensions["cpa_account_session_schema_ready"]

        # A fixture-only hook after the production Principal loader. No debug
        # endpoint or delay switch is installed by the production application.
        @app.before_request
        def pause_loaded_request():
            if request.path == "/api/auth/me" and request.headers.get("X-Test-Hold") == nonce:
                assert session.get("_user_id")
                if gate["modified"]:
                    session["test_race_marker"] = True
                gate["entered"].set()
                assert gate["release"].wait(15), "test request was not released"

        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield {"app": app, "base": f"http://127.0.0.1:{server.server_port}",
               "accounts": accounts, "gate": gate, "nonce": nonce,
               "cache": cache, "mysql": mysql}
    finally:
        gate["release"].set()
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=5)
        patch.undo()
        if mysql:
            mysql.close()
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database}`")
                cursor.execute("SHOW DATABASES LIKE %s", (database,))
                assert cursor.fetchone() is None
        admin.close()
        keys = list(cache.scan_iter())
        if keys:
            cache.delete(*keys)
        assert cache.dbsize() == 0
        cache.close()


@pytest.mark.parametrize("modified", [False, True], ids=["read-only", "session-write"])
def test_late_request_cannot_restore_logout_or_replace_new_login(logout_runtime, modified):
    rt = logout_runtime
    base, gate = rt["base"], rt["gate"]
    gate["entered"].clear()
    gate["release"].clear()
    gate["modified"] = modified
    # Preserve real rate limiting while isolating independent test runs.
    rt["app"].config["RATELIMIT_KEY_PREFIX"] = "logout-" + uuid.uuid4().hex
    browsers = [requests.Session() for _ in range(4)]
    owner, other_device, delayed, replay = browsers
    for browser in browsers:
        browser.trust_env = False

    def login(browser, role):
        token = browser.get(base + "/api/auth/csrf", timeout=5).json()["csrfToken"]
        account = rt["accounts"][role]
        result = browser.post(base + "/api/auth/login", json={
            "username": account["username"], "password": account["password"]},
            headers={"X-CSRFToken": token}, timeout=5)
        assert result.status_code == 200
        assert result.headers["Cache-Control"] == "no-store"
        assert result.json()["user"]["id"] == account["id"]
        return token

    try:
        token = login(owner, "student")
        login(other_device, "student")
        old_cookie = owner.cookies.get("cpa_session")
        old_key = "cpa:account:session:" + old_cookie
        delayed.cookies.update(owner.cookies)
        replay.cookies.update(owner.cookies)
        epoch_before = rt["mysql"].execute_single(
            "SELECT auth_epoch FROM user_account WHERE id=:id", rt["accounts"]["student"]
        )["auth_epoch"]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(delayed.get, base + "/api/auth/me",
                                     headers={"X-Test-Hold": rt["nonce"]}, timeout=20)
            try:
                assert gate["entered"].wait(5)
                assert owner.post(base + "/api/auth/logout",
                                  headers={"X-CSRFToken": token}, timeout=5).status_code == 200
                assert not rt["cache"].exists(old_key)
                assert owner.get(base + "/api/auth/me", timeout=5).status_code == 401
                login(owner, "admin")
            finally:
                gate["release"].set()
            late = future.result(timeout=10)
        assert late.status_code == 200  # An already-admitted read may finish.
        if rt["cache"].exists(old_key):
            pytest.fail("late response resurrected the logged-out Redis SID")
        if "cpa_session=" in late.headers.get("Set-Cookie", ""):
            pytest.fail("late response resets the old cookie")
        assert replay.get(base + "/api/auth/me", timeout=5).status_code == 401
        student_id = rt["accounts"]["student"]["id"]
        assert replay.get(base + f"/api/profile/student/{student_id}/basic", timeout=5).status_code == 401
        assert owner.get(base + "/api/auth/me", timeout=5).json()["user"]["id"] == rt["accounts"]["admin"]["id"]
        assert other_device.get(base + "/api/auth/me", timeout=5).json()["user"]["id"] == student_id
        epoch_after = rt["mysql"].execute_single(
            "SELECT auth_epoch FROM user_account WHERE id=:id", {"id": student_id})["auth_epoch"]
        assert epoch_before == epoch_after
    finally:
        gate["release"].set()
        for browser in browsers:
            browser.close()
