"""The three legacy ext skill routes share the source-verified public reader."""

import pytest
from flask import Flask

from app.api import pipeline_ext_extract  # registers the target routes
from app.api.pipeline_ext_common import pipeline_ext_bp
from app.services import public_skill_graph as public
from app.pipeline.entity_extractor import graph_storage


ROUTES = [
    ("/recommend", {"skills": "Python", "limit": 5}),
    ("/path", {"from": "Python", "to": "机器学习"}),
    ("/graph", {"skills": "Python,机器学习"}),
]


@pytest.fixture
def client(monkeypatch):
    # A future accidental fallback to GraphStorage fails the HTTP request.
    monkeypatch.setattr(graph_storage, "GraphStorage", lambda: (_ for _ in ()).throw(AssertionError("legacy graph fallback")))
    app = Flask(__name__)
    app.register_blueprint(pipeline_ext_bp)
    return app.test_client()


class PartitionDB:
    def __init__(self, count=11, fail=False):
        self.count = count
        self.fail = fail
        self.calls = []

    def execute_query(self, cypher, parameters=None, read_only=True):
        self.calls.append((cypher, parameters))
        if self.fail:
            raise ConnectionError("private database connection information")
        assert read_only is True
        assert ":PublicSkill" in cypher and ":Skill" not in cypher
        assert parameters["source_id"] == public.SOURCE_ID
        assert "victim" not in parameters.values()
        assert "labels(" in cypher and "visibility" in cypher
        if "RETURN count(s) AS count" in cypher:
            return [{"count": self.count}]
        if "nodes(path)" in cypher:
            assert "all(n IN nodes(path)" in cypher
            assert "all(r IN relationships(path)" in cypher
            return [{"steps": ["Python", "机器学习"], "hops": 1}]
        assert "r.source_id = $source_id" in cypher
        if " AS source," in cypher:
            return [{"source": "机器学习", "target": "Python", "relation": "PREREQUISITE",
                     "confidence": 0.9, "source_id": public.SOURCE_ID}]
        return [{"name": "机器学习", "category": "算法", "weight": 1, "source_id": public.SOURCE_ID}]


@pytest.mark.parametrize("route,query", ROUTES)
def test_ext_skill_routes_query_only_public_partition_and_ignore_forged_scope(client, monkeypatch, route, query):
    db = PartitionDB()
    monkeypatch.setattr(public, "get_neo4j_client", lambda: db)
    response = client.get("/api/pipeline/ext" + route, query_string={
        **query, "student_id": "victim", "source_id": "victim", "scope": "private",
    })
    assert response.status_code == 200
    assert response.json["success"] is True
    assert response.json["data"]["scope"] == "public_seed"
    assert response.json["data"]["source_id"] == public.SOURCE_ID
    assert "victim" not in response.get_data(as_text=True)
    assert len(db.calls) == 2


@pytest.mark.parametrize("route,query", ROUTES)
@pytest.mark.parametrize("mode", ["unpublished", "db_down"])
def test_ext_skill_routes_never_fallback_to_mixed_graph(client, monkeypatch, route, query, mode):
    db = PartitionDB(count=0 if mode == "unpublished" else 11, fail=mode == "db_down")
    monkeypatch.setattr(public, "get_neo4j_client", lambda: db)
    response = client.get("/api/pipeline/ext" + route, query_string=query)
    assert response.status_code == 503
    assert response.json["success"] is False
    assert response.json["status"] == "unavailable"
    assert "private database connection information" not in response.get_data(as_text=True)
    assert len(db.calls) == 1


@pytest.mark.parametrize("route,query", [
    ("/recommend", {"skills": "P" * 81}),
    ("/recommend", {"skills": ",,"}),
    ("/graph", {"skills": ",".join("s" + str(i) for i in range(51))}),
    ("/path", {"from": "Python", "to": "机器学习", "max_hops": "1 MATCH (p:Person) RETURN p"}),
    ("/path", {"from": "Python", "to": "机器学习", "max_hops": 99}),
])
def test_ext_skill_routes_reject_unbounded_or_invalid_input_before_database(client, monkeypatch, route, query):
    monkeypatch.setattr(public, "get_neo4j_client", lambda: (_ for _ in ()).throw(AssertionError("invalid input reached DB")))
    response = client.get("/api/pipeline/ext" + route, query_string=query)
    assert response.status_code == 400
    assert response.json["success"] is False
