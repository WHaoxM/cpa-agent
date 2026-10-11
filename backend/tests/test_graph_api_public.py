"""Public graph routes must not cross into unowned JobPortrait/Person data."""

from flask import Flask, g
from neo4j.graph import Graph, Node

from app.api import graph_bp
from app.api import graph as graph_api
from app.api._helpers import Principal
from app.config import Config


def _app():
    app = Flask(__name__)
    app.register_blueprint(graph_bp, url_prefix="/api/graph")
    return app


def test_unscoped_skill_and_job_portrait_templates_fail_closed():
    client = _app().test_client()
    for template in graph_api.DISABLED_PRIVATE_TEMPLATES:
        response = client.post("/api/graph/query", json={
            "template": template, "params": {"portrait_id": "portrait_private"},
        })
        assert response.status_code == 410
        assert response.json["success"] is False
    assert client.get("/api/graph/career-path/portrait_private").status_code == 410
    assert client.get("/api/graph/job-requirements/portrait_private").status_code == 410
    for template in graph_api.SAFE_CYPHER_TEMPLATES.values():
        assert "JobPortrait" not in template["cypher"]
        assert "Skill" not in template["cypher"]
        assert "NOT c1:Person" in template["cypher"]


def test_skill_neighbor_and_category_routes_fail_closed_without_database(monkeypatch):
    monkeypatch.setattr(graph_api, "get_neo4j_client", lambda: (_ for _ in ()).throw(AssertionError("db used")))
    client = _app().test_client()
    assert client.get("/api/graph/skill-neighbors/Python").status_code == 503
    assert client.get("/api/graph/skill-categories").status_code == 503
    assert client.get("/api/graph/overview").status_code == 410


def test_public_career_query_projects_only_allowlisted_scalars(monkeypatch):
    class FakeDB:
        def execute_query(self, cypher, params, read_only=True):
            assert "CareerNode" in cypher
            assert params == {"position_name": "初级前端工程师"}
            assert read_only is True
            return [{
                "start": "初级前端工程师", "end": "高级前端工程师", "steps": 2,
                "student_id": "student_private",
            }]

    monkeypatch.setattr(graph_api, "get_neo4j_client", lambda: FakeDB())
    response = _app().test_client().post("/api/graph/query", json={
        "template": "career_path", "params": {"position_name": "初级前端工程师"},
    })
    assert response.status_code == 200
    assert response.json["data"]["rows"][0]["steps"] == 2
    assert "student_private" not in response.get_data(as_text=True)


def test_query_does_not_render_mixed_person_career_labels(monkeypatch):
    node = Node(Graph(), "person1", 1, ["Person", "CareerNode"], {
        "name": "private person", "entity_id": "student_private",
    })

    class FakeDB:
        def execute_query(self, *_args, **_kwargs):
            return [{"s": node}]

    monkeypatch.setattr(graph_api, "get_neo4j_client", lambda: FakeDB())
    response = _app().test_client().post("/api/graph/query", json={
        "template": "career_path", "params": {"position_name": "初级前端工程师"},
    })
    assert response.status_code == 200
    assert response.json["data"]["nodes"] == []
    assert "student_private" not in response.get_data(as_text=True)


def test_public_projection_allowlist_has_no_shared_skill_or_person_types():
    assert graph_api.PUBLIC_NODE_LABELS == frozenset({"CareerNode"})
    assert graph_api.PUBLIC_REL_TYPES == frozenset({"PROMOTION", "TRANSFER"})
    assert not ({"name", "category", "aliases", "student_id"} & graph_api.PUBLIC_NODE_FIELDS)
    assert graph_api.PUBLIC_SCALAR_FIELDS == frozenset({"start", "end", "steps"})


def test_build_requires_principal_in_strict_mode(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    response = _app().test_client().post("/api/graph/build", json={
        "student_id": "student_attack", "type": "full",
    })
    assert response.status_code == 401


def test_build_binds_student_id_from_principal_not_request_body(monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    calls = []

    class FakeMySQL:
        def execute(self, sql, params):
            assert params == {"task_id": "task_real", "student_id": "student_owner"}
            return [{"task_id": "task_real", "status": "completed"}]

    class FakeRunner:
        mysql = FakeMySQL()

        def run(self, level, params):
            calls.append((level, params))
            return {"student_id": params["student_id"], "task_id": "task_real"}

    monkeypatch.setattr(pipeline, "PipelineRunner", FakeRunner)
    app = _app()

    @app.before_request
    def _principal():
        g.cpa_principal = Principal("principal_owner", "student_owner", "test")

    response = app.test_client().post("/api/graph/build", json={
        "student_id": "student_attack", "type": "full", "profile_text": "test",
    })
    assert response.status_code == 200
    assert calls[0][1]["student_id"] == "student_owner"
    assert response.json["data"]["task_id"] == "task_real"
