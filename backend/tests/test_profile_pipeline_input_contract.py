"""Active profile pipeline adapter validates its envelope before execution."""

import pytest
from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config


@pytest.mark.parametrize("body", [["L2_generate_portrait"], 42, "L2_generate_portrait"])
def test_active_pipeline_rejects_non_object_json_before_runner(monkeypatch, body):
    from app.services import pipeline

    monkeypatch.setattr(Config, "DEMO_MODE", False)

    def must_not_execute():
        raise AssertionError("bad request must not construct PipelineRunner")

    monkeypatch.setattr(pipeline, "PipelineRunner", must_not_execute)
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = Principal("owner", "owner", "test")

    response = app.test_client().post("/api/profile/student/pipeline", json=body)
    assert response.status_code == 400
    assert response.json["success"] is False


@pytest.mark.parametrize("field,value", [
    ("job_id", ["job_1"]),
    ("job_portrait_id", {"id": "job_1"}),
    ("title", ["draft"]),
])
def test_active_pipeline_rejects_invalid_optional_fields_before_runner(monkeypatch, field, value):
    from app.services import pipeline

    monkeypatch.setattr(Config, "DEMO_MODE", False)

    def must_not_execute():
        raise AssertionError("bad request must not construct PipelineRunner")

    monkeypatch.setattr(pipeline, "PipelineRunner", must_not_execute)
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = Principal("owner", "owner", "test")

    response = app.test_client().post("/api/profile/student/pipeline", json={
        "pipeline": "L2_generate_portrait", field: value,
    })
    assert response.status_code == 400
    assert response.json["success"] is False
