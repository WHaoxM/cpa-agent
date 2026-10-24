"""Owner-bound account profile and notification-preference HTTP contracts."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

from redis import Redis


class _Mappings:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return deepcopy(self.row)


class _Result(_Mappings):
    def __init__(self, row=None, rowcount=0):
        super().__init__(row)
        self.rowcount = rowcount


class _Connection:
    def __init__(self, database):
        self.database = database

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if sql.startswith("SELECT id,username,name,email,phone,signature,role,status"):
            row = self.database.accounts.get(params["id"])
            return _Result(row)
        if sql.startswith("SELECT email_preference,system_preference,sms_preference,revision"):
            return _Result(self.database.preferences.get(params["id"]))
        if sql.startswith("INSERT INTO user_account_notification_preference"):
            self.database.preferences.setdefault(params["id"], {
                "email_preference": None,
                "system_preference": None,
                "sms_preference": None,
                "revision": 0,
            })
            return _Result(rowcount=1)
        if sql.startswith("UPDATE user_account SET"):
            row = self.database.accounts[params["id"]]
            if row["profile_revision"] != params["revision"]:
                return _Result(rowcount=0)
            if self.database.fail_next_profile_update:
                self.database.fail_next_profile_update = False
                raise RuntimeError("isolated test write failure")
            for field in ("name", "email", "phone", "signature"):
                row[field] = params[field]
            row["profile_revision"] += 1
            row["updated_at"] = datetime(2026, 10, 24, 17, 0)
            return _Result(rowcount=1)
        if sql.startswith("UPDATE user_account_notification_preference SET"):
            row = self.database.preferences[params["id"]]
            if row["revision"] != params["revision"]:
                return _Result(rowcount=0)
            row.update({
                "email_preference": params["email"],
                "system_preference": params["system"],
                "sms_preference": params["sms"],
                "revision": row["revision"] + 1,
            })
            return _Result(rowcount=1)
        raise AssertionError(f"Unexpected isolated test SQL: {sql}")


class _Engine:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def begin(self):
        before = deepcopy((self.database.accounts, self.database.preferences))
        try:
            yield _Connection(self.database)
        except Exception:
            self.database.accounts, self.database.preferences = before
            raise


class _Database:
    def __init__(self):
        self.accounts = {
            "acct_test_a": {
                "id": "acct_test_a", "username": "test_a", "password_hash": "unused",
                "auth_method": "account_password",
                "name": "Student A", "email": None, "phone": None, "signature": None,
                "role": "student", "status": "active", "auth_epoch": 0,
                "profile_revision": 0, "created_at": datetime(2025, 5, 1, 9, 0),
                "last_login_at": datetime(2026, 10, 24, 16, 55),
            },
            "acct_test_b": {
                "id": "acct_test_b", "username": "test_b", "password_hash": "unused",
                "auth_method": "account_password",
                "name": "Student B", "email": "b@example.test", "phone": None,
                "signature": None, "role": "student", "status": "active",
                "auth_epoch": 0, "profile_revision": 0,
                "created_at": datetime(2025, 8, 3, 10, 0),
                "last_login_at": None,
            },
        }
        self.preferences = {}
        self.fail_next_profile_update = False
        self.engine = _Engine(self)

    def execute_single(self, sql, params=None):
        params = params or {}
        if "FROM user_account_notification_preference" in sql:
            return deepcopy(self.preferences.get(params.get("id")))
        lookup_id = params.get("id", params.get("value"))
        account = next((row for row in self.accounts.values()
                        if row["id"] == lookup_id
                        or row["username"] == lookup_id), None)
        if account is None:
            return None
        if "FROM user_account WHERE id=:id LIMIT 1" in sql:
            return deepcopy(account)
        return deepcopy(account)


class _MemoryRedis(Redis):
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, name, value, ex=None, xx=False):
        if xx and name not in self.values:
            return None
        self.values[name] = value
        return True

    def delete(self, key):
        self.values.pop(key, None)
        return 1


def _build_app(monkeypatch):
    from redis import Redis

    from app import create_app
    from app.config import Config
    from app.services import account_session_schema
    from app.services.agent_runtime import schema_ensure
    from app.api import auth

    database = _Database()
    memory_redis = _MemoryRedis()
    monkeypatch.setattr(Redis, "from_url", lambda *args, **kwargs: memory_redis)
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr(account_session_schema, "account_session_schema_ready", lambda: True)
    monkeypatch.setattr(schema_ensure, "ensure_runtime_schema", lambda: None)
    monkeypatch.setattr(auth.limiter, "init_app", lambda app: None)

    class TestAuthConfig(Config):
        TESTING = True
        DEMO_MODE = False
        SECRET_KEY = "isolated-account-profile-test-secret-long-enough"
        CPA_AUTH_COOKIE_SECURE = False
        REDIS_URL = "redis://127.0.0.1:1/0"

    app = create_app(TestAuthConfig)
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setattr(auth, "get_mysql_client", lambda: database)
    return app, database


def _authenticated_client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True
        session["_cpa_auth_epoch"] = 0
    token_response = client.get("/api/auth/csrf")
    assert token_response.status_code == 200
    return client, token_response.json["csrfToken"]


def test_profile_load_save_retry_refresh_and_account_isolation(monkeypatch):
    app, database = _build_app(monkeypatch)
    anonymous = app.test_client().get("/api/auth/profile")
    assert anonymous.status_code == 401

    client_a, csrf_a = _authenticated_client(app, "acct_test_a")
    client_b, csrf_b = _authenticated_client(app, "acct_test_b")
    initial = client_a.get("/api/auth/profile")
    assert initial.status_code == 200
    assert initial.json["data"]["profile"] == {
        "id": "acct_test_a", "username": "test_a", "name": "Student A",
        "email": None, "phone": None, "signature": None, "role": "student",
        "status": "active", "created_at": "2025-05-01T09:00:00",
        "last_login_at": "2026-10-24T16:55:00", "auth_method": "account_password",
        "revision": 0,
    }

    rejected = client_a.patch("/api/auth/profile", json={
        "expected_revision": 0, "email": "not-an-email", "id": "acct_test_b",
    }, headers={"X-CSRFToken": csrf_a})
    assert rejected.status_code == 400
    assert database.accounts["acct_test_a"]["email"] is None
    invalid_phone = client_a.patch("/api/auth/profile", json={
        "expected_revision": 0, "phone": "+++",
    }, headers={"X-CSRFToken": csrf_a})
    assert invalid_phone.status_code == 400
    assert database.accounts["acct_test_a"]["phone"] is None

    # A database failure returns an error and leaves the draft retryable.
    database.fail_next_profile_update = True
    failed = client_a.patch("/api/auth/profile", json={
        "expected_revision": 0, "name": "Student A updated",
        "email": "a@example.test", "phone": "13800000000", "signature": "A profile",
    }, headers={"X-CSRFToken": csrf_a})
    assert failed.status_code == 503
    assert database.accounts["acct_test_a"]["profile_revision"] == 0

    saved = client_a.patch("/api/auth/profile", json={
        "expected_revision": 0, "name": "Student A updated",
        "email": "a@example.test", "phone": "13800000000", "signature": "A profile",
    }, headers={"X-CSRFToken": csrf_a})
    assert saved.status_code == 200
    assert saved.json["data"]["profile"]["revision"] == 1
    refreshed = client_a.get("/api/auth/profile")
    assert refreshed.json["data"]["profile"]["phone"] == "13800000000"
    assert refreshed.json["data"]["profile"]["signature"] == "A profile"

    # A second page that loaded revision 0 cannot overwrite the first save.
    stale = client_a.patch("/api/auth/profile", json={
        "expected_revision": 0, "name": "stale overwrite",
    }, headers={"X-CSRFToken": csrf_a})
    assert stale.status_code == 409
    assert database.accounts["acct_test_a"]["name"] == "Student A updated"

    # Another signed-in account sees only its own row and can clear a missing field.
    own_b = client_b.get("/api/auth/profile")
    assert own_b.status_code == 200
    assert own_b.json["data"]["profile"]["id"] == "acct_test_b"
    assert own_b.json["data"]["profile"]["phone"] is None
    assert own_b.json["data"]["profile"]["email"] == "b@example.test"
    own_b_update = client_b.patch("/api/auth/profile", json={
        "expected_revision": 0, "signature": "B only",
    }, headers={"X-CSRFToken": csrf_b})
    assert own_b_update.status_code == 200
    assert database.accounts["acct_test_a"]["signature"] == "A profile"
    assert database.accounts["acct_test_b"]["signature"] == "B only"


def test_notification_preferences_persist_intent_without_enabling_channels(monkeypatch):
    app, database = _build_app(monkeypatch)
    client, csrf = _authenticated_client(app, "acct_test_a")

    initial = client.get("/api/auth/notification-preferences")
    assert initial.status_code == 200
    assert initial.json["data"]["configured"] is False
    assert initial.json["data"]["revision"] == 0
    assert initial.json["data"]["preferences"] == {"email": None, "system": None, "sms": None}
    assert all(not channel["available"] for channel in initial.json["data"]["channels"].values())

    saved = client.put("/api/auth/notification-preferences", json={
        "expected_revision": 0,
        "preferences": {"email": True, "system": None, "sms": False},
    }, headers={"X-CSRFToken": csrf})
    assert saved.status_code == 200
    assert saved.json["data"]["configured"] is True
    assert saved.json["data"]["preferences"] == {"email": True, "system": None, "sms": False}
    assert all(not channel["available"] for channel in saved.json["data"]["channels"].values())
    assert database.preferences["acct_test_a"]["revision"] == 1

    refreshed = client.get("/api/auth/notification-preferences")
    assert refreshed.json["data"]["preferences"] == {"email": True, "system": None, "sms": False}
    stale = client.put("/api/auth/notification-preferences", json={
        "expected_revision": 0,
        "preferences": {"email": False, "system": False, "sms": False},
    }, headers={"X-CSRFToken": csrf})
    assert stale.status_code == 409
    assert database.preferences["acct_test_a"]["email_preference"] is True
