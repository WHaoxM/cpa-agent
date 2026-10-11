"""Personal graph is a gated capability, not a second conversation or agent."""

import json
import os
import uuid

import pytest
from flask import Flask, g

from app.api._helpers import Principal
from app.config import Config
from app.services import personal_graph as personal
from app.services.tools.graph_query import GraphQueryTool


class Client:
    def __init__(self, snapshots=None, rows=None):
        self.snapshots = snapshots if snapshots is not None else [{"snapshot_id": "snapshot", "source_ref": "profile:confirmed:1"}]
        self.rows = rows if rows is not None else [{"name": "Python", "category": "语言", "evidence": "项目使用 Python", "source_ref": "profile:confirmed:1"}]
        self.calls = []

    def execute_query(self, query, parameters=None, read_only=True):
        self.calls.append((query, parameters.copy(), read_only))
        assert read_only is True
        assert parameters["owner_student_id"] == "owner"
        assert parameters["source_id"] == personal.SOURCE_ID
        if query == personal.INTEGRITY_QUERY:
            return [{"edge_count": 1, "valid_count": 1, "unique_count": 1}]
        return self.snapshots if query == personal.SNAPSHOT_QUERY else self.rows


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr(personal, "confirmed_projection", lambda mysql, owner: {
        "owner_student_id": owner, "source_id": personal.SOURCE_ID, "snapshot_id": "snapshot",
        "source_ref": "profile:confirmed:1", "source_task_id": "task", "content_sha256": "digest", "skills": [{}],
    })
    return Flask(__name__)


def invoke(app, client, **kwargs):
    with app.test_request_context():
        g.cpa_principal = Principal("actor", "owner", "test")
        return json.loads(GraphQueryTool(client).execute(query="我的技能图谱", query_type="personal_skills", **kwargs))


def test_missing_principal_never_uses_model_owner(app):
    client = Client()
    tool = GraphQueryTool(client)
    assert json.loads(tool.execute(query="我的图谱", query_type="personal_skills", student_id="owner"))["status"] == "denied"
    with app.test_request_context():
        assert json.loads(tool.execute(query="我的图谱", query_type="personal_skills", student_id="owner"))["status"] == "denied"
    assert client.calls == []


def test_explicit_template_is_owner_bound_and_never_accepts_cypher(app):
    client = Client()
    name = "x' MATCH (p:Person) RETURN p //"
    result = invoke(app, client, entity_name=name, student_id="victim", owner_student_id="victim", cypher="MATCH (p) RETURN p")
    assert result["status"] == "completed"
    assert result["scope"] == "personal"
    assert "Python" in result["message"]
    assert client.calls[2] == (personal.SKILLS_QUERY, {
        "owner_student_id": "owner", "source_id": personal.SOURCE_ID,
        "snapshot_id": "snapshot", "source_ref": "profile:confirmed:1",
        "entity_name": name, "offset": 0, "fetch_limit": 11,
        "source_task_id": "task", "content_sha256": "digest", "skills": [{}],
    }, True)
    assert name not in client.calls[2][0]


@pytest.mark.parametrize("snapshots", [[], [{"snapshot_id": "a", "source_ref": "a"}, {"snapshot_id": "b", "source_ref": "b"}]])
def test_missing_or_ambiguous_verified_snapshot_is_unavailable(app, snapshots):
    client = Client(snapshots=snapshots)
    result = invoke(app, client)
    assert result["success"] is False and result["status"] == "unavailable"
    assert "不能据此判断你没有技能" in result["message"]
    assert len(client.calls) == 1


@pytest.mark.parametrize("offset", [True, -1, 1.2, "1 DELETE n", "-1", "10001", "１"])
def test_bad_offsets_do_not_touch_database(app, offset):
    client = Client()
    assert invoke(app, client, offset=offset)["status"] == "failed"
    assert client.calls == []


def test_cursor_and_scalar_projection_are_explicit(app):
    client = Client(rows=[{"name": f"Skill {i}", "category": "test", "evidence": "evidence", "source_ref": "profile:confirmed:1", "private": "hidden"} for i in range(11)])
    result = invoke(app, client, offset="10")
    assert result["status"] == "partial" and result["truncated"] is True
    assert result["displayed_count"] == 10 and result["next_offset"] == 20
    assert "offset=20" in result["continuation"]
    assert "hidden" not in json.dumps(result)
    assert client.calls[-1][1]["offset"] == 10


def test_model_can_choose_personal_template_but_cannot_supply_owner(app):
    class LLM:
        def chat(self, **kwargs):
            return '{"template":"personal_skills","entity_name":""}'

    client = Client()
    with app.test_request_context():
        g.cpa_principal = Principal("actor", "owner", "test")
        result = json.loads(GraphQueryTool(client, LLM()).execute(query="我的图谱有哪些技能？"))
    assert result["status"] == "completed"
    assert GraphQueryTool._parse_selection('{"template":"personal_skills","owner_student_id":"victim"}') is None


def test_personal_window_stays_below_egress_budget_and_cursor_covers_omitted_rows(app):
    from app.services.agent_runtime.tool_result_channels import build_model_view

    client = Client(rows=[{"name": "n" * 200, "category": "c" * 200, "evidence": "e" * 200, "source_ref": "r" * 200}] * 11)
    result = invoke(app, client)
    assert 0 < result["displayed_count"] < 10
    assert result["next_offset"] == result["displayed_count"]
    text = build_model_view("graph_query", json.dumps(result))
    assert len(text) < 4000
    assert f"offset={result['next_offset']}" in text
    assert "字段截断" in text


def test_langchain_adapter_and_existing_gate_keep_pagination_and_hide_owner(app, monkeypatch):
    from app.services import langgraph_agent
    from app.services.agent_runtime import gated_tools
    from app.services.agent_runtime.modes import InteractionMode

    class NoopCommitter:
        def commit_candidates(self, *args, **kwargs):
            return []

    monkeypatch.setattr(gated_tools, "enqueue_audit", lambda **kwargs: None)
    monkeypatch.setattr(gated_tools, "get_artifact_committer", lambda: NoopCommitter())
    client = Client(rows=[{"name": "Python", "evidence": "a", "source_ref": "b"}] * 11)
    previous = langgraph_agent._tool_registry.pop("graph_query", None)
    try:
        adapted = langgraph_agent._adapt_existing_tool(GraphQueryTool(client))
        schema = adapted.args_schema.model_json_schema()
        assert "owner_student_id" not in schema["properties"]
        assert "student_id" not in schema["properties"]
        assert "offset" in schema["properties"]
        assert {item.get("type") for item in schema["properties"]["offset"].get("anyOf", [])} >= {"integer"}
        ctx = gated_tools.make_gate_context(interaction_mode=InteractionMode.AGENT, subagent="qa", student_id="owner", session_id="session")
        ui = []
        wrapped = gated_tools._wrap_one(adapted, ctx, ui)
        with app.test_request_context():
            g.cpa_principal = Principal("actor", "owner", "test")
            result = wrapped.invoke({"query": "我的技能", "query_type": "personal_skills", "offset": 10, "owner_student_id": "victim"})
        assert "Python" in result and "offset=20" in result
        assert client.calls[-1][1]["offset"] == 10
        assert ui[0]["status"] == "partial"
        assert ctx.denials == []
    finally:
        langgraph_agent._tool_registry.pop("graph_query", None)
        if previous is not None:
            langgraph_agent._tool_registry["graph_query"] = previous


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_PERSONAL_GRAPH") != "1", reason="explicit live Neo4j opt-in required")
def test_live_personal_scope_and_public_partition_isolation(app, monkeypatch):
    """Creates only UUID-labelled fixtures; cleans exactly those records in finally."""
    from app.services.db import get_neo4j_client
    from app.services.public_skill_graph import query_public_skills

    client = get_neo4j_client()
    token = "personal-scope-test-" + uuid.uuid4().hex
    owner, victim = token + "-owner", token + "-victim"
    params = {"token": token, "owner": owner, "victim": victim, "source": personal.SOURCE_ID}
    monkeypatch.setattr(personal, "confirmed_projection", lambda mysql, sid: {
        "owner_student_id": sid, "source_id": personal.SOURCE_ID, "snapshot_id": sid,
        "source_ref": sid, "source_task_id": sid, "content_sha256": sid,
        "skills": [{"skill_id": str(i), "name": sid + '-skill-' + str(i), "evidence": 'fixture evidence'} for i in range(11)],
    })
    try:
        # Bare legacy entity_id is not ownership, even when it equals Principal.
        client.execute_query("CREATE (:Person {entity_id: $owner, test_run: $token})", params, read_only=False)
        with app.test_request_context():
            g.cpa_principal = Principal(owner, owner, "test")
            result = json.loads(GraphQueryTool(client).execute(query="我的技能", query_type="personal_skills"))
            assert result["status"] == "unavailable"
        client.execute_query("""
            UNWIND [$owner, $victim] AS owner
            CREATE (p:PersonalGraphSnapshot {owner_student_id: owner, source_id: $source,
                provenance_version: 1, state: 'verified', visibility: 'private',
                snapshot_id: owner, source_ref: owner, source_task_id: owner, content_sha256: owner, test_run: $token})
            WITH p, owner UNWIND range(0, 10) AS i
            CREATE (s:PersonalSkill {owner_student_id: owner, source_id: $source,
                provenance_version: 1, visibility: 'private', snapshot_id: owner, source_ref: owner, source_task_id: owner, content_sha256: owner,
                name: owner + '-skill-' + toString(i), skill_id: toString(i), evidence: 'fixture evidence', test_run: $token})
            CREATE (p)-[:HAS_PERSONAL_SKILL {owner_student_id: owner, source_id: $source,
                provenance_version: 1, snapshot_id: owner, source_ref: owner, source_task_id: owner, content_sha256: owner}]->(s)
        """, params, read_only=False)
        with app.test_request_context():
            g.cpa_principal = Principal(owner, owner, "test")
            tool = GraphQueryTool(client)
            first = json.loads(tool.execute(query="技能", query_type="personal_skills", student_id=victim))
            assert first["status"] == "partial" and first["next_offset"] == 10
            assert victim not in first["message"]
            last = json.loads(tool.execute(query="技能", query_type="personal_skills", offset="10"))
            assert last["status"] == "completed" and last["row_count"] == 1
            empty = json.loads(tool.execute(query="技能", query_type="personal_skills", entity_name=victim + "-skill-0"))
            assert empty["row_count"] == 0
        # One changed variable at a time: edge owner, provenance, node owner,
        # private marker, source_ref and mixed labels must each exclude a row.
        mutations = [
            ("SET r.owner_student_id = $victim", "SET r.owner_student_id = $owner"),
            ("SET r.source_id = 'untrusted'", "SET r.source_id = $source"),
            ("SET s.owner_student_id = $victim", "SET s.owner_student_id = $owner"),
            ("SET s.visibility = 'public'", "SET s.visibility = 'private'"),
            ("SET s.source_ref = 'wrong'", "SET s.source_ref = $owner"),
            ("SET s:PublicSkill", "REMOVE s:PublicSkill"),
        ]
        prefix = "MATCH (p:PersonalGraphSnapshot {snapshot_id: $owner})-[r:HAS_PERSONAL_SKILL]->(s:PersonalSkill {snapshot_id: $owner, skill_id: '0'}) "
        for mutation, restore in mutations:
            client.execute_query(prefix + mutation, params, read_only=False)
            result = personal.query_personal_skills(client, owner_student_id=owner, entity_name=owner + "-skill-0")
            assert result["status"] == "unavailable"
            client.execute_query(prefix + restore, params, read_only=False)
        client.execute_query("""
            MATCH (p:PersonalGraphSnapshot {snapshot_id: $owner}), (s:PersonalSkill {snapshot_id: $victim})
            CREATE (p)-[:HAS_PERSONAL_SKILL {owner_student_id: $owner, source_id: $source,
                provenance_version: 1, snapshot_id: $owner, source_ref: $owner}]->(s)
        """, params, read_only=False)
        assert personal.query_personal_skills(client, owner_student_id=owner)["status"] == "unavailable"
        public = query_public_skills("skills", client=client)
        assert token not in json.dumps(public)
    finally:
        client.execute_query("MATCH (n {test_run: $token}) DETACH DELETE n", {"token": token}, read_only=False)
