"""Old L3 endpoints must not accept caller-owned scores in strict mode."""

from flask import Flask, g
import pytest

from app.api._helpers import Principal
from app.api.pipeline_ext_common import pipeline_ext_bp
from app.config import Config


@pytest.fixture
def legacy_client(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(pipeline_ext_bp, url_prefix="/api/pipeline/ext")

    @app.before_request
    def bind_principal():
        g.cpa_principal = Principal("owner", "owner", "test")

    return app.test_client()


@pytest.mark.parametrize("method,path,body", [
    ("post", "/save/match-result", {"student_id": "victim", "job_portrait_id": "job", "total_score": 99}),
    ("post", "/save/skill-gap", {"student_id": "victim", "job_portrait_id": "job"}),
    ("post", "/save/career-path", {"student_id": "victim", "job_portrait_id": "job"}),
    ("post", "/match/full", {"student_id": "victim", "job_portrait_id": "job",
                             "student_skills": ["Python"], "job_skills": ["Python"]}),
    ("get", "/match/list?student_id=victim", None),
    ("get", "/match/trend?student_id=victim", None),
])
def test_legacy_l3_endpoints_retired_in_strict_mode_without_touching_db(
    monkeypatch, legacy_client, method, path, body,
):
    from app.services import db

    monkeypatch.setattr(db, "get_mysql_client", lambda: (_ for _ in ()).throw(
        AssertionError("retired route must not touch DB")))
    response = getattr(legacy_client, method)("/api/pipeline/ext" + path, json=body)
    assert response.status_code == 410
    assert response.json["status"] == "legacy_disabled"
    assert response.json["next_action"] == "use_verified_match_api"


def test_legacy_l3_requires_auth_before_retirement_response(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(pipeline_ext_bp, url_prefix="/api/pipeline/ext")
    response = app.test_client().get("/api/pipeline/ext/match/list")
    assert response.status_code == 401
