"""Strict mode must not infer identity from client input or a default user."""

from flask import Flask, jsonify

from app.api._helpers import ScopeError, resolve_request_scope
from app.config import Config
from app.middleware.dev_auth import DevAuthMiddleware


def _app():
    app = Flask(__name__)
    DevAuthMiddleware(app)

    @app.get("/whoami")
    def whoami():
        try:
            return jsonify({"student_id": resolve_request_scope().student_id})
        except ScopeError as exc:
            return jsonify({"error": exc.message}), exc.status_code

    return app


def test_strict_mode_dev_auth_is_off_by_default(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.delenv("CPA_DEV_AUTH", raising=False)
    client = _app().test_client()
    assert client.get("/whoami").status_code == 401
    assert client.get("/whoami?student_id=someone_else").status_code == 401
    assert client.get("/whoami", headers={"X-Dev-Student-Id": "someone_else"}).status_code == 401


def test_opted_in_dev_auth_requires_loopback_and_explicit_header(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setenv("CPA_DEV_AUTH", "1")
    client = _app().test_client()
    assert client.get("/whoami").status_code == 401
    assert client.get("/whoami?student_id=someone_else").status_code == 401
    ok = client.get("/whoami", headers={"X-Dev-Student-Id": "student_local"})
    assert ok.status_code == 200
    assert ok.get_json()["student_id"] == "student_local"
    remote = client.get(
        "/whoami",
        headers={"X-Dev-Student-Id": "student_remote"},
        environ_base={"REMOTE_ADDR": "203.0.113.7"},
    )
    assert remote.status_code == 401
