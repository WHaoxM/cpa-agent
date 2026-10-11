from flask import g

from app import create_app
from app.config import Config


def _scoped_client(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    return app.test_client()


def test_match_owned_paths_reject_other_student_before_database(monkeypatch):
    from app.api import match as match_api

    client = _scoped_client(monkeypatch)
    monkeypatch.setattr(match_api, "get_mysql_client", lambda: (_ for _ in ()).throw(
        AssertionError("foreign path must not query database")))
    paths = (
        "/result/victim_B/job_1",
        "/skill-gap/victim_B/job_1",
        "/explain/victim_B/job_1",
        "/career-recommendation/victim_B",
        "/list/victim_B",
    )
    for path in paths:
        response = client.get("/api/match" + path)
        assert response.status_code == 403, path


def test_match_owner_queries_are_parameterized_with_principal(monkeypatch):
    from app.api import match as match_api

    client = _scoped_client(monkeypatch)
    calls = []

    class FakeMySQL:
        def execute(self, sql, params=None):
            calls.append((sql, params or {}))
            return []

    monkeypatch.setattr(match_api, "get_mysql_client", FakeMySQL)
    for path in ("/result/owner_A/job_1", "/skill-gap/owner_A/job_1",
                 "/explain/owner_A/job_1", "/career-recommendation/owner_A",
                 "/list/owner_A"):
        response = client.get("/api/match" + path)
        assert response.status_code in (200, 404), path
    assert calls
    assert all(params.get("student_id") == "owner_A" for _, params in calls)
    assert all(":student_id" in sql for sql, _ in calls)


def test_match_run_uses_principal_and_returns_real_task_id(monkeypatch):
    client = _scoped_client(monkeypatch)
    from app.services import pipeline as service_pipeline

    seen = {}

    class FakeRunner:
        def run(self, pipeline, params):
            seen.update(params)
            return {"task_id": "task_stored", "total_score": 75, "status": "partial"}

    monkeypatch.setattr(service_pipeline, "PipelineRunner", FakeRunner)
    response = client.post("/api/match/run", json={
        "student_id": "victim_B", "job_portrait_id": "job_1"
    })
    assert response.status_code == 200
    assert seen == {"student_id": "owner_A", "job_portrait_id": "job_1"}
    assert response.get_json()["data"]["task_id"] == "task_stored"
    assert response.get_json()["status"] == "partial"
    assert response.get_json()["success"] is False


def test_match_owned_paths_fail_closed_without_principal(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    client = create_app().test_client()
    assert client.get("/api/match/explain/owner_A/job_1").status_code == 401
    assert client.post("/api/match/run", json={"job_portrait_id": "job_1"}).status_code == 401
