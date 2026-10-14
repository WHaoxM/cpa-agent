"""Malformed learning progress requests stop before querying course state."""

import pytest
from flask import g

from app import create_app
from app.api import learning as learning_api
from app.api._helpers import Principal
from app.config import Config


@pytest.mark.parametrize("body", [["wrong"], 17, "wrong", None])
def test_progress_requires_json_object_after_owner_resolution(monkeypatch, body):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_owner():
        g.cpa_principal = Principal("owner", "owner", "test")

    monkeypatch.setattr(learning_api, "get_mysql_client", lambda: (_ for _ in ()).throw(
        AssertionError("invalid input must not query MySQL")))
    response = app.test_client().post("/api/learning/learning/progress", json=body)
    assert response.status_code == 400
    assert response.json["success"] is False


def test_progress_unauthenticated_request_fails_before_input_diagnostic(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    response = create_app().test_client().post(
        "/api/learning/learning/progress", json=["wrong"])
    assert response.status_code == 401
