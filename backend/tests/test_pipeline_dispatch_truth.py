"""Legacy pipeline starts must not create tasks that no worker can consume."""

import os
import uuid

import pytest
from flask import g, request

from app import create_app
from app.api._helpers import Principal
from app.config import Config


def _client(monkeypatch, *, admin=False):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = Principal("operator" if admin else "owner_A", "owner_A", "test")
        if admin:
            g.cpa_admin = True

    return app.test_client()


@pytest.mark.parametrize("path,body", [
    ("/api/pipeline/trigger", {"pipeline": "L1_build_graph"}),
    ("/api/pipeline/l1/build-graph", {}),
    ("/api/pipeline/l2/generate-portrait", {"job_id": "job_1"}),
    ("/api/pipeline/l4/generate-report", {"job_portrait_id": "portrait_1"}),
])
def test_strict_legacy_start_fails_before_creating_permanent_pending_task(monkeypatch, path, body):
    from app.api import pipeline as pipeline_api

    def db_must_not_be_touched():
        raise AssertionError("No durable task without an executor")

    monkeypatch.setattr(pipeline_api, "get_mysql_client", db_must_not_be_touched)
    client = _client(monkeypatch)
    listing = client.get("/api/pipeline/list")
    assert listing.status_code == 200
    assert {item["trigger_mode"] for item in listing.json["data"]} == {"unavailable", "dedicated_async"}
    response = client.post(path, json=body)

    assert response.status_code == 503
    assert response.json["success"] is False
    assert response.json["code"] == "pipeline_dispatch_unavailable"
    assert "task_id" not in response.json.get("data", {})


def test_l3_rejects_missing_confirmed_owner_before_creating_intent(monkeypatch):
    from app.api import pipeline as pipeline_api
    from app.services import pipeline as domain

    monkeypatch.setattr(pipeline_api, "get_mysql_client", lambda: object())
    def no_profile(_db, _owner):
        raise ValueError("未确认简历")
    monkeypatch.setattr(domain, "require_confirmed_profile", no_profile)
    response = _client(monkeypatch).post("/api/pipeline/l3/run-match", json={"job_portrait_id": "portrait_1"})
    assert response.status_code == 409
    assert response.json["success"] is False
    assert "task_id" not in response.json.get("data", {})


def test_admin_build_fails_before_creating_permanent_pending_task(monkeypatch):
    from app.api import admin as admin_api

    def db_must_not_be_touched():
        raise AssertionError("No durable task without an executor")

    monkeypatch.setattr(admin_api, "get_mysql_client", db_must_not_be_touched)
    response = _client(monkeypatch, admin=True).post("/api/admin/pipeline/build", json={
        "source": "dataset", "source_id": "dataset_1", "pipeline_type": "full",
        "triggered_by": "forged-browser-identity",
    })

    assert response.status_code == 503
    assert response.json["success"] is False
    assert response.json["code"] == "admin_pipeline_build_unavailable"
    assert "task_id" not in response.json.get("data", {})


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_strict_legacy_starts_leave_no_task_rows_in_live_mysql(monkeypatch):
    from app.services.db.mysql_client import get_mysql_client

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    owner = "dispatch_" + uuid.uuid4().hex[:8]
    db = get_mysql_client()
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = Principal(owner, owner, "live-test")
        if request.path.startswith("/api/admin/"):
            g.cpa_admin = True

    def task_count():
        rows = db.fetch_all(
            "SELECT COUNT(*) AS total FROM pipeline_task "
            "WHERE student_id=:owner OR triggered_by=:owner",
            {"owner": owner},
        )
        return int(rows[0]["total"])

    assert task_count() == 0
    client = app.test_client()
    for path, body in (
        ("/api/pipeline/trigger", {"pipeline": "L1_build_graph"}),
        ("/api/pipeline/l1/build-graph", {}),
        ("/api/pipeline/l2/generate-portrait", {"job_id": "job_1"}),
        ("/api/pipeline/l3/run-match", {"job_portrait_id": "portrait_1"}),
        ("/api/pipeline/l4/generate-report", {"job_portrait_id": "portrait_1"}),
        ("/api/admin/pipeline/build", {"source_id": "dataset_1"}),
    ):
        response = client.post(path, json=body)
        assert response.status_code == (409 if path == "/api/pipeline/l3/run-match" else 503), (path, response.json)
        assert task_count() == 0, path
