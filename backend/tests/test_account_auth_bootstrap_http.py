"""HTTP bootstrap contract for platform-managed account authentication."""


def test_csrf_http_requires_strong_secret_and_ready_account_schema(monkeypatch):
    """Exercise the public CSRF route without creating accounts or touching DBs."""
    from redis import Redis

    from app import create_app
    from app.config import Config
    from app.middleware.account_auth import _DEFAULT_SECRET
    from app.services import account_session_schema
    from app.services.agent_runtime import schema_ensure

    class MemoryRedis(Redis):
        """Only the Flask-Session operations exercised by this route."""

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

    memory_redis = MemoryRedis()
    monkeypatch.setattr(Redis, "from_url", lambda *args, **kwargs: memory_redis)

    # Keep strict mode active while replacing only the schema readiness check;
    # this isolates the auth bootstrap contract from local MySQL/Redis state.
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr(account_session_schema, "account_session_schema_ready", lambda: True)
    monkeypatch.setattr(schema_ensure, "ensure_runtime_schema", lambda: None)

    strong_config = type("StrongSecretAuthConfig", (Config,), {
        "SECRET_KEY": "regression-test-secret-that-is-longer-than-32-bytes",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "REDIS_URL": "redis://127.0.0.1:1/0",
    })
    strong_app = create_app(strong_config)
    assert strong_app.extensions["cpa_account_auth_enabled"] is True
    assert strong_app.extensions["cpa_account_session_schema_ready"] is True
    client = strong_app.test_client()
    response = client.get("/api/auth/csrf")
    assert response.status_code == 200
    assert response.json["success"] is True
    assert isinstance(response.json["csrfToken"], str) and response.json["csrfToken"]
    assert response.headers["Cache-Control"] == "no-store"
    identity = client.get("/api/auth/me")
    assert identity.status_code == 401
    assert identity.headers["Cache-Control"] == "no-store"

    missing_secret_config = type("MissingSecretAuthConfig", (Config,), {
        "SECRET_KEY": _DEFAULT_SECRET,
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
    })
    missing_secret_app = create_app(missing_secret_config)
    assert missing_secret_app.extensions["cpa_account_auth_enabled"] is False
    rejected = missing_secret_app.test_client().get("/api/auth/csrf")
    assert rejected.status_code == 503
    assert rejected.headers["Cache-Control"] == "no-store"
