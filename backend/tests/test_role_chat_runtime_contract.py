"""Do not advertise a broken role runtime or trust body-supplied identity."""

from types import SimpleNamespace

from flask import Flask, g

from app.api._helpers import Principal
from app.api.roles import roles_bp
from app.config import Config


def _client(principal=None):
    app = Flask(__name__)
    app.register_blueprint(roles_bp)
    if principal is not None:
        @app.before_request
        def bind_principal():
            g.cpa_principal = principal
    return app.test_client()


def test_strict_role_chat_ignores_body_student_id(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    response = _client().post("/api/roles/chat", json={
        "role_id": "career_coach", "message": "你好", "student_id": "someone_else",
    })
    assert response.status_code == 401


def test_known_role_returns_explicit_not_wired_not_a_500(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    client = _client(Principal("principal", "student_a", "test"))
    response = client.post("/api/roles/chat", json={
        "role_id": "career_coach", "message": "你好", "student_id": "student_b",
    })
    assert response.status_code == 501
    assert response.get_json()["code"] == "ROLE_CHAT_NOT_WIRED"


def test_role_chat_checks_session_owner_before_unavailable(monkeypatch):
    import app.services.session_manager as session_manager

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr(session_manager, "get_session_manager", lambda: SimpleNamespace(
        get_session=lambda _sid: SimpleNamespace(student_id="student_b"),
    ))
    client = _client(Principal("principal", "student_a", "test"))
    response = client.post("/api/roles/chat", json={
        "role_id": "career_coach", "message": "你好", "session_id": "session_b",
    })
    assert response.status_code == 403
