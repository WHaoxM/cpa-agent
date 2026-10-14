"""Live HTTP/Neo4j checks for the public CareerNode projection."""

import uuid

import pytest
from flask import Flask

from app import create_app
from app.api import graph as graph_api, graph_bp
from app.config import Config
from app.services.db import get_neo4j_client


@pytest.fixture
def graph_client(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    return create_app().test_client()


@pytest.mark.parametrize("params", [None, [], {}, {"position_name": "x", "extra": 1}, {"position_name": " "}, {"position_name": 7}])
def test_career_path_rejects_malformed_parameters_as_client_errors(monkeypatch, params):
    app = Flask(__name__)
    app.register_blueprint(graph_bp, url_prefix="/api/graph")
    client = app.test_client()
    def unexpected_database_access():
        raise AssertionError("invalid request must be rejected before Neo4j")

    monkeypatch.setattr(graph_api, "get_neo4j_client", unexpected_database_access)
    response = client.post("/api/graph/query", json={
        "template": "career_path", "params": params,
    })
    assert response.status_code == 400
    assert response.json["success"] is False


@pytest.mark.parametrize("body", [["career_path"], 3, {"template": ["career_path"]}])
def test_graph_query_rejects_non_object_or_non_text_template_before_database(monkeypatch, body):
    app = Flask(__name__)
    app.register_blueprint(graph_bp, url_prefix="/api/graph")
    client = app.test_client()

    def unexpected_database_access():
        raise AssertionError("invalid request must be rejected before Neo4j")

    monkeypatch.setattr(graph_api, "get_neo4j_client", unexpected_database_access)
    response = client.post("/api/graph/query", json=body)
    assert response.status_code == 400
    assert response.json["success"] is False


def test_public_career_path_live_neo4j_scope_and_empty_shape(graph_client):
    neo4j = get_neo4j_client()
    token = uuid.uuid4().hex
    start = f"acceptance_start_{token}"
    end = f"acceptance_end_{token}"
    private_marker = f"private_{token}"
    try:
        neo4j.execute_query(
            "MERGE (s:CareerNode {position_name:$start}) "
            "SET s.secret_marker=$private "
            "MERGE (e:CareerNode {position_name:$end}) "
            "MERGE (s)-[r:PROMOTION]->(e) SET r.years=2, r.secret_marker=$private "
            "MERGE (p:CareerNode:Person {position_name:$start}) "
            "SET p.student_id=$private "
            "MERGE (p)-[:PROMOTION]->(e)",
            {"start": start, "end": end, "private": private_marker},
            read_only=False,
        )

        response = graph_client.post("/api/graph/query", json={
            "template": "career_path", "params": {"position_name": start},
        })
        assert response.status_code == 200
        body = response.json
        assert body["success"] is True
        assert body["data"] == {
            "nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0,
            "rows": [{"start": start, "end": end, "steps": 1}], "total_rows": 1,
        }
        assert private_marker not in response.get_data(as_text=True)

        missing = graph_client.post("/api/graph/query", json={
            "template": "career_path", "params": {"position_name": f"missing_{token}"},
        })
        assert missing.status_code == 200
        assert missing.json["success"] is True
        assert missing.json["data"]["rows"] == []
        assert missing.json["data"]["total_rows"] == 0
    finally:
        neo4j.execute_query(
            "MATCH (n) WHERE n.position_name IN [$start, $end] DETACH DELETE n",
            {"start": start, "end": end},
            read_only=False,
        )
        residual = neo4j.execute_query(
            "MATCH (n) WHERE n.position_name IN [$start, $end] RETURN count(n) AS n",
            {"start": start, "end": end},
        )
        assert residual == [{"n": 0}]
