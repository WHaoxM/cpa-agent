"""Career path must be a verified-match/public-graph projection, not old client data."""

from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config
import pytest


def test_strict_career_path_never_replays_legacy_client_written_json(monkeypatch):
    from app.api import match as api

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def owner():
        g.cpa_principal = Principal("owner", "owner", "test")

    class DB:
        def execute(self, sql, params=None, commit=False):
            if "FROM career_path" in sql:
                return [{"id": 1, "student_id": "owner", "job_portrait_id": "job",
                         "recommended_dir": "FORGED_DIRECTION", "path_json": '["FORGED_PATH"]'}]
            if "FROM match_result" in sql:
                return []
            raise AssertionError(sql)

        def fetch_all(self, sql, params=None):
            raise AssertionError("No L3 rows; no additional lookup should be needed")

    monkeypatch.setattr(api, "get_mysql_client", DB)
    response = app.test_client().get("/api/match/career-recommendation/owner")
    assert response.status_code == 404
    assert "FORGED" not in response.get_data(as_text=True)


def test_explicit_demo_retains_legacy_sample_shape(monkeypatch):
    from app.api import match as api

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo-owner")
    app = create_app()

    class DB:
        def execute(self, sql, params=None, commit=False):
            assert "FROM career_path" in sql
            assert params["student_id"] == "demo-owner"
            return [{"student_id": "demo-owner", "job_portrait_id": "demo-job",
                     "recommended_dir": "sample", "path_json": '["sample-step"]'}]

    monkeypatch.setattr(api, "get_mysql_client", DB)
    response = app.test_client().get("/api/match/career-recommendation/demo-owner")
    assert response.status_code == 200
    assert response.json["data"]["paths"] == ["sample-step"]


@pytest.fixture
def strict_path_client(monkeypatch):
    from app.api import match as api
    from app.api import graph as graph_api
    from app.services.agent_runtime import match_report_resources

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def owner():
        g.cpa_principal = Principal("owner", "owner", "test")

    seen = {"queries": [], "read_ids": [], "graph_names": []}

    class DB:
        def execute(self, sql, params=None, commit=False):
            assert "FROM career_path" not in sql
            seen["queries"].append((sql, params))
            if params.get("job_portrait_id") not in (None, "job"):
                return []
            return [{"id": 7, "job_portrait_id": "job"}]

    def verified(_mysql, *, student_id, uri):
        assert student_id == "owner"
        seen["read_ids"].append(uri)
        return {"business_artifact_verified": True, "status": "partial",
                "data": {"position_name": "Verified role"}}

    def graph(template, params):
        assert template == "career_path"
        seen["graph_names"].append(params["position_name"])
        return [{"start": "Verified role", "end": "Senior role", "steps": 1},
                {"start": "injected", "end": "Private role", "steps": 1},
                {"start": "Verified role", "end": "Out of bound", "steps": 9}]

    monkeypatch.setattr(api, "get_mysql_client", DB)
    monkeypatch.setattr(match_report_resources, "read_match_report_resource", verified)
    monkeypatch.setattr(graph_api, "_execute_safe_query", graph)
    return app.test_client(), seen, monkeypatch


def test_strict_career_path_uses_verified_snapshot_exact_public_name(strict_path_client):
    client, seen, _ = strict_path_client
    response = client.get("/api/match/career-recommendation/owner?job_portrait_id=job")
    assert response.status_code == 200
    body = response.json
    assert body["status"] == "partial"
    assert body["data"]["position_name"] == "Verified role"
    assert body["data"]["recommended_dir"] is None
    assert body["data"]["personalized_recommendation_verified"] is False
    assert body["data"]["paths"] == [{"start": "Verified role", "end": "Senior role", "steps": 1}]
    assert seen["read_ids"] == ["career://me/matches/7"]
    assert seen["graph_names"] == ["Verified role"]
    assert seen["queries"][0][1] == {"student_id": "owner", "job_portrait_id": "job"}


def test_strict_career_path_stale_match_never_queries_graph(strict_path_client):
    from app.services.agent_runtime import match_report_resources

    client, seen, monkeypatch = strict_path_client
    monkeypatch.setattr(match_report_resources, "read_match_report_resource", lambda *args, **kwargs: {
        "business_artifact_verified": False, "reason": "unverified_or_stale_record",
        "next_action": "refresh_source_records",
    })
    response = client.get("/api/match/career-recommendation/owner")
    assert response.status_code == 409
    assert response.json["status"] == "stale"
    assert seen["graph_names"] == []
    assert "Verified role" not in response.get_data(as_text=True)


def test_strict_career_path_graph_empty_and_failure_are_explicit_partial(strict_path_client):
    from app.api import graph as graph_api

    client, _, monkeypatch = strict_path_client
    monkeypatch.setattr(graph_api, "_execute_safe_query", lambda *args: [])
    empty = client.get("/api/match/career-recommendation/owner")
    assert empty.status_code == 200
    assert empty.json["status"] == "partial"
    assert empty.json["data"]["paths"] == []
    assert empty.json["data"]["reason"] == "exact_public_promotion_path_unavailable"

    def broken(*args):
        raise ConnectionError("private diagnostic must not leak")

    monkeypatch.setattr(graph_api, "_execute_safe_query", broken)
    failed = client.get("/api/match/career-recommendation/owner")
    assert failed.status_code == 200
    assert failed.json["status"] == "partial"
    assert failed.json["data"]["unavailable_sources"] == ["public_career_graph"]
    assert "private diagnostic" not in failed.get_data(as_text=True)


def test_strict_career_path_rejects_foreign_owner_and_bad_job_id(strict_path_client):
    client, seen, _ = strict_path_client
    assert client.get("/api/match/career-recommendation/other").status_code == 403
    assert client.get("/api/match/career-recommendation/owner?job_portrait_id=%20").status_code == 400
    assert seen["queries"] == []
