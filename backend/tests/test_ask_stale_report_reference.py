from types import SimpleNamespace

from flask import Flask

from app.api import agent
from app.services.resume_diagnostic_report import ResumeReportStale


def test_stale_report_reference_returns_conflict_not_internal_error(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr(agent, "resolve_request_scope", lambda **kwargs: SimpleNamespace(student_id="owner"))
    monkeypatch.setattr(agent, "get_session_manager", lambda: object())
    monkeypatch.setattr(agent, "_resolve_session", lambda *args: (
        "session", SimpleNamespace(student_id="owner"),
    ))

    def stale(*args, **kwargs):
        raise ResumeReportStale("old confirmed version")

    monkeypatch.setattr("app.services.agent_runtime.media.prepare_turn.prepare_user_turn", stale)
    with app.test_request_context(json={"message": "read", "interaction_mode": "ask",
                                       "content_parts": [{"type": "resource_ref", "uri": "career://me/resume-reports/old"}]}):
        response, status = agent.chat()
    assert status == 409
    assert response.json["code"] == "RESOURCE_VERSION_CONFLICT"
    assert response.json["next_action"] == "refresh_resource_reference"


def test_owner_mismatch_returns_not_found_before_resource_read(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr(agent, "resolve_request_scope", lambda **kwargs: SimpleNamespace(student_id="owner"))
    monkeypatch.setattr(agent, "get_session_manager", lambda: object())
    monkeypatch.setattr(agent, "_resolve_session", lambda *args: (
        "session", SimpleNamespace(student_id="other-owner"),
    ))

    def must_not_read(*args, **kwargs):
        raise AssertionError("resource read must not run for a foreign session")

    monkeypatch.setattr("app.services.agent_runtime.media.prepare_turn.prepare_user_turn", must_not_read)
    with app.test_request_context(json={"message": "read", "interaction_mode": "ask",
                                       "content_parts": [{"type": "resource_ref", "uri": "career://me/resume-reports/old"}]}):
        response, status = agent.chat()
    assert status == 404
    assert response.json["code"] == "NOT_FOUND"
