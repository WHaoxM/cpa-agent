"""Admin metadata endpoints reject malformed envelopes before MySQL."""

import pytest
from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config


@pytest.mark.parametrize("resource,body", [
    ("dataset", ["not-an-object"]),
    ("dataset", {"category": "test"}),
    ("dataset", {"title": ["invalid"]}),
    ("dataset", {"title": "valid", "category": ["invalid"]}),
    ("knowledge-base", ["not-an-object"]),
    ("knowledge-base", {"type": "text"}),
    ("knowledge-base", {"title": ["invalid"]}),
    ("knowledge-base", {"title": "valid", "type": ["invalid"]}),
])
def test_metadata_upload_rejects_invalid_input_before_db(monkeypatch, resource, body):
    from app.api import admin as admin_api

    monkeypatch.setattr(Config, "DEMO_MODE", False)

    def must_not_access_db():
        raise AssertionError("invalid metadata must not reach MySQL")

    monkeypatch.setattr(admin_api, "get_mysql_client", must_not_access_db)
    app = create_app()

    @app.before_request
    def bind_admin():
        g.cpa_principal = Principal("admin", "owner", "test")
        g.cpa_admin = True

    response = app.test_client().post(f"/api/admin/{resource}/upload", json=body)
    assert response.status_code == 400
    assert response.json["success"] is False
