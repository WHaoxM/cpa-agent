"""Partition contract tests; opt-in live test exercises real Neo4j predicates."""

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
import importlib.util
import json

import pytest
from flask import Flask, g

from app.api import graph_bp
from app.api import graph as graph_api
from app.api._helpers import Principal
from app.config import Config
from app.services import public_skill_graph as public
from app.services.tools.db_query import DbQueryTool
from app.services.tools.graph_query import GraphQueryTool
from app.services.tools.semantic_search import SemanticSearchTool
from app.services.tools.skill_tools import SkillRecommendTool, LearningPathTool


class GraphDouble:
    def __init__(self, count=11, rows=None):
        self.count = count
        self.rows = rows or [{"name": "机器学习", "category": "算法", "source_id": public.SOURCE_ID}]
        self.calls = []

    def execute_query(self, cypher, parameters=None, read_only=True):
        self.calls.append((cypher, parameters, read_only))
        assert read_only
        assert ":PublicSkill" in cypher
        assert ":Skill" not in cypher
        assert "labels(" in cypher and "visibility" in cypher
        assert parameters["source_id"] == public.SOURCE_ID
        if "RETURN count(s) AS count" in cypher:
            return [{"count": self.count}]
        return self.rows


def test_unpublished_partition_is_unavailable_not_an_empty_search():
    client = GraphDouble(count=0)
    result = public.query_public_skills("skills", client=client)
    assert result["success"] is False
    assert result["status"] == "unavailable"
    assert "不能据此判断" in result["message"]
    assert len(client.calls) == 1


def test_public_skill_tool_api_and_recommendation_share_partition(monkeypatch):
    client = GraphDouble()
    monkeypatch.setattr(public, "get_neo4j_client", lambda: client)
    monkeypatch.setattr(graph_api, "get_neo4j_client", lambda: client)
    tool = GraphQueryTool(neo4j_client=client)
    text = tool.execute(query="Python 邻居", query_type="skill_neighbors", entity_name="Python")
    assert "机器学习" in text and public.SOURCE_ID in text
    recommendation = SkillRecommendTool().execute(["Python"])
    assert recommendation["scope"] == "public_seed"
    assert recommendation["recommendations"][0]["name"] == "机器学习"
    app = Flask(__name__)
    app.register_blueprint(graph_bp, url_prefix="/api/graph")
    response = app.test_client().get("/api/graph/skill-neighbors/Python")
    assert response.status_code == 200
    assert response.json["data"]["scope"] == "public_seed"
    assert response.json["data"]["rows"][0]["name"] == "机器学习"


def test_public_api_reports_unpublished_partition_as_503(monkeypatch):
    monkeypatch.setattr(graph_api, "get_neo4j_client", lambda: GraphDouble(count=0))
    app = Flask(__name__)
    app.register_blueprint(graph_bp, url_prefix="/api/graph")
    response = app.test_client().get("/api/graph/skill-categories")
    assert response.status_code == 503
    assert response.json["data"]["status"] == "unavailable"


def test_learning_path_filters_every_intermediate_and_edge(monkeypatch):
    client = GraphDouble(rows=[{"steps": ["Python", "机器学习"], "hops": 1}])
    monkeypatch.setattr(public, "get_neo4j_client", lambda: client)
    result = LearningPathTool().execute("Python", "机器学习")
    assert result["steps"] == ["Python", "机器学习"]
    query = client.calls[-1][0]
    assert "all(n IN nodes(path)" in query
    assert "all(r IN relationships(path)" in query
    assert "<-[:PREREQUISITE*1..5]-" in query


@pytest.mark.parametrize("params", [
    {"skill_names": ["Python"], "limit": "1 DELETE n"},
    {"skill_names": ["Python"], "limit": 100000},
    {"skill_names": ["Python"], "limit": 1, "source_id": "private"},
])
def test_public_query_rejects_executable_or_scope_parameters(params):
    client = GraphDouble()
    with pytest.raises(ValueError):
        public.query_public_skills("recommend", params, client=client)
    assert not client.calls


def test_offline_publisher_writes_only_reviewed_seed_in_one_transaction():
    path = Path(__file__).resolve().parents[1] / "scripts" / "seed_public_skills.py"
    spec = importlib.util.spec_from_file_location("public_skill_seed_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Transaction:
        def run(self, cypher, **params):
            calls.append((cypher, params))
            assert ":Skill" not in cypher and ":Person" not in cypher
            assert "MERGE" in cypher and ":PublicSkill" in cypher
            assert params["source_id"] == public.SOURCE_ID
            return self

        def consume(self):
            return None

    class Session:
        def execute_write(self, callback):
            callback(Transaction())

    class Client:
        @contextmanager
        def session(self):
            yield Session()

    result = module.publish(Client())
    assert result == {"source_id": public.SOURCE_ID, "skills": 11, "relations": 4}
    assert len(calls) == 2
    assert len(calls[0][1]["skills"]) == 11
    assert all(set(row) == {"name", "category"} for row in calls[0][1]["skills"])


def test_runtime_skill_writer_cannot_modify_public_partition():
    from app.pipeline.entity_extractor.graph_storage import GraphStorage
    calls = []

    class Session:
        def run(self, cypher, parameters=None):
            calls.append((cypher, parameters))
            assert "PublicSkill" not in cypher
            return []

    # The runtime writer uses hardcoded labels/fields, not extraction-supplied
    # visibility/source_id/labels. Test a forged publication marker explicitly.
    storage = object.__new__(GraphStorage)
    session = Session()
    skills = [{"name": "private secret", "source_id": public.SOURCE_ID,
               "visibility": "public", "labels": ["PublicSkill"]}]
    storage._batch_save_skill_nodes(session, skills)
    storage._batch_save_entity_skill_relations(session, "owner", "student", skills)
    storage._save_relation(session, {"source": "private secret", "target": "Python", "type": "prerequisite"})
    assert len(calls) == 3
    assert "source_id" not in calls[0][1]["skills"][0]
    assert "visibility" not in calls[0][1]["skills"][0]


@pytest.mark.parametrize("collection", ["all", "resume", "conversation", "skill", "jd", "secret_collection", "skill,resume"])
def test_semantic_search_never_queries_unowned_or_arbitrary_collections(collection):
    class Poison:
        def __getattr__(self, key):
            raise AssertionError("unpartitioned storage accessed")
    tool = SemanticSearchTool(milvus_client=Poison())
    tool._embedding = Poison()
    output = tool.execute(query="resume secret", collection=collection, student_id="victim")
    assert "不可用" in output and "不能据此判断" in output
    assert json.loads(output)["success"] is False
    assert json.loads(output)["status"] == "unavailable"


class SQLDouble:
    def __init__(self):
        self.calls = []

    def execute(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        assert parameters["student_id"] == "owner"
        assert "student_id = :student_id" in sql
        return []


def test_db_tool_denies_forged_owner_without_request():
    client = SQLDouble()
    result = DbQueryTool(client).execute(query="学生画像", student_id="victim")
    assert "权限拒绝" in result
    assert json.loads(result)["success"] is False
    assert json.loads(result)["status"] == "denied"
    assert not client.calls


@pytest.mark.parametrize("query,params", [
    ("学生画像", {"student_id": "victim"}),
    ("对话", {"student_id": "victim", "session_id": "victim_session"}),
    ("历史报告", {"student_id": "victim", "report_id": "victim_report"}),
])
def test_db_tool_uses_server_principal_in_sql_not_tool_owner(monkeypatch, query, params):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    client = SQLDouble()
    with Flask(__name__).test_request_context():
        g.cpa_principal = Principal("actor", "owner", "test")
        DbQueryTool(client).execute(query=query, **params)
    assert len(client.calls) == 1
    sql, bindings = client.calls[0]
    assert bindings["student_id"] == "owner"
    if query == "对话":
        assert "JOIN conversation c" in sql


def test_db_tool_rejects_missing_principal_and_unsafe_table_fallback(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    client = SQLDouble()
    with Flask(__name__).test_request_context():
        assert "权限拒绝" in DbQueryTool(client).execute(query="学生画像", student_id="victim")
    assert "不可用" in DbQueryTool(client).execute(query="岗位画像")
    with pytest.raises(ValueError):
        DbQueryTool(client)._build_query("pipeline_task", "unknown", {})
    assert not client.calls


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_PUBLIC_GRAPH") != "1", reason="explicit live Neo4j opt-in required")
def test_live_neo4j_public_skill_partition_excludes_private_and_mixed_labels():
    """Self-cleaning fixture; no existing legacy or published records are modified."""
    from app.services.db import get_neo4j_client
    client = get_neo4j_client()
    token = "partition-test-" + uuid.uuid4().hex
    params = {"token": token, "source_id": public.SOURCE_ID}
    try:
        client.execute_query(
            "CREATE (s:PublicSkill {name: $token, source_id: $source_id, visibility: 'public', test_run: $token}), "
            "(good:PublicSkill {name: $token + '-good', source_id: $source_id, visibility: 'public', test_run: $token}), "
            "(legacy:Skill {name: $token + '-secret', test_run: $token}), "
            "(mixed:PublicSkill:Person {name: $token + '-private', source_id: $source_id, visibility: 'public', test_run: $token}), "
            "(bad:PublicSkill {name: $token + '-wrong-source', source_id: 'untrusted', visibility: 'public', test_run: $token}), "
            "(s)-[:PREREQUISITE {source_id: $source_id}]->(good), "
            "(s)-[:PREREQUISITE {source_id: $source_id}]->(legacy), "
            "(s)-[:PREREQUISITE {source_id: $source_id}]->(mixed), "
            "(s)-[:PREREQUISITE {source_id: $source_id}]->(bad)", params, read_only=False,
        )
        result = public.query_public_skills("skill_neighbors", {"entity_name": token}, client=client)
        assert [r["name"] for r in result["rows"]] == [token + "-good"]
        result = public.query_public_skills("recommend", {"skill_names": [token], "limit": 50}, client=client)
        assert [r["name"] for r in result["rows"]] == [token + "-good"]
        result = public.query_public_skills("learning_path", {"from_skill": token + "-good", "to_skill": token}, client=client)
        assert result["rows"][0]["steps"] == [token + "-good", token]
    finally:
        client.execute_query("MATCH (n {test_run: $token}) DETACH DELETE n", {"token": token}, read_only=False)
