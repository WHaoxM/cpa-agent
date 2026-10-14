"""Real registered favorites routes must reject malformed writes before persistence."""

import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from flask import Flask, g
from werkzeug.serving import make_server

from app.api import favorites_bp
from app.api._helpers import Principal
from app.config import Config


@pytest.fixture
def favorites_app(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(favorites_bp, url_prefix="/api")

    @app.before_request
    def identity():
        g.cpa_principal = Principal("test:owner", "owner", "test")

    return app


@pytest.mark.parametrize("path", ["/api/saved-jobs", "/api/target-roles"])
@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("not json", "text/plain"),
        ("{broken", "application/json"),
        ("42", "application/json"),
    ],
)
def test_invalid_write_body_is_client_error_without_repository_call(
    favorites_app, monkeypatch, path, body, content_type
):
    from app.api import favorites

    def unexpected_write(**_kwargs):
        pytest.fail("invalid request must not reach persistence")

    monkeypatch.setattr(favorites.favorites_repo, "save_job", unexpected_write)
    monkeypatch.setattr(favorites.favorites_repo, "add_target_role", unexpected_write)
    response = favorites_app.test_client().post(
        path, data=body, content_type=content_type
    )
    assert response.status_code == 400
    assert response.json["success"] is False


def test_invalid_write_body_over_socket(favorites_app, monkeypatch):
    from app.api import favorites

    calls = []
    monkeypatch.setattr(favorites.favorites_repo, "save_job", lambda **kwargs: calls.append(kwargs))
    server = make_server("127.0.0.1", 0, favorites_app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/saved-jobs",
            data=b"42",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        assert error.value.code == 400
        assert calls == []
    finally:
        server.shutdown()
        worker.join(timeout=5)


def test_target_role_rejects_non_text_name_before_repository(favorites_app, monkeypatch):
    from app.api import favorites

    monkeypatch.setattr(
        favorites.favorites_repo,
        "add_target_role",
        lambda **_kwargs: pytest.fail("invalid role must not reach persistence"),
    )
    response = favorites_app.test_client().post(
        "/api/target-roles", json={"role_name": ["工程师"]}
    )
    assert response.status_code == 400
    assert response.json["success"] is False
