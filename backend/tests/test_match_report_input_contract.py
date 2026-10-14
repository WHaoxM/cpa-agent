"""Match/report adapters reject malformed JSON before creating persistent work."""

import pytest
from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config


@pytest.mark.parametrize("path", ["/api/match/run", "/api/report/generate"])
@pytest.mark.parametrize("body", [["job_1"], 42, "job_1"])
def test_match_report_reject_non_object_before_runner_or_persistence(monkeypatch, path, body):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = Principal("owner", "owner", "test")

    from app.services import pipeline
    monkeypatch.setattr(pipeline, "PipelineRunner", lambda: (_ for _ in ()).throw(
        AssertionError("invalid body reached PipelineRunner")))
    client = app.test_client()
    response = client.post(path, json=body)
    assert response.status_code == 400


@pytest.mark.parametrize("path", ["/api/match/run", "/api/report/generate"])
@pytest.mark.parametrize("value", [["job_1"], {"id": "job_1"}, 7, True, "   ", "x" * 65])
def test_match_report_reject_invalid_job_id_before_runner_or_persistence(monkeypatch, path, value):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = Principal("owner", "owner", "test")

    from app.services import pipeline
    monkeypatch.setattr(pipeline, "PipelineRunner", lambda: (_ for _ in ()).throw(
        AssertionError("invalid job id reached PipelineRunner")))
    response = app.test_client().post(path, json={"job_id": value})
    assert response.status_code == 400
