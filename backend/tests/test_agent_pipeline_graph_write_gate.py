"""A student-scoped tool is not authority to mutate the shared graph."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.agent_runtime import gated_tools
from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.pre_tool_use import GateContext, pre_tool_use
from app.services.tools.pipeline_run import PipelineRunTool


class TaskStore:
    def __init__(self):
        self.writes = []

    def execute(self, sql, params=None, commit=False):
        self.writes.append((sql, params))


def invoke_tool(monkeypatch, tool, params, *, mode=InteractionMode.AGENT):
    from app.services import langgraph_agent

    monkeypatch.setattr(langgraph_agent, "_tool_registry", {})
    monkeypatch.setattr(gated_tools, "enqueue_audit", lambda **kwargs: "audit-test")
    monkeypatch.setattr(gated_tools, "get_artifact_committer", lambda: SimpleNamespace(
        commit_candidates=lambda *args, **kwargs: []))
    ctx = GateContext(interaction_mode=mode, subagent="profile", student_id="owner_A",
                      session_id="session_A", plan_tool_allowlist={"pipeline_run"})
    ui = []
    wrapped = gated_tools._wrap_one(langgraph_agent._adapt_existing_tool(tool), ctx, ui)
    result = wrapped.invoke({"pipeline": "L1_build_graph", "params": json.dumps(params)})
    return result, ui, ctx


@pytest.mark.parametrize("mode", [InteractionMode.AGENT, InteractionMode.EXPERTS])
@pytest.mark.parametrize("build_type", ["skill_skeleton", "student_skills"])
def test_graph_build_denied_before_task_creation(monkeypatch, mode, build_type):
    tool = PipelineRunTool()
    tool._mysql = TaskStore()
    monkeypatch.setattr(tool, "_execute_pipeline", lambda *args, **kwargs: {
        "status": "completed", "summary": "unsafe graph writer reached"})
    result, ui, ctx = invoke_tool(monkeypatch, tool, {"type": build_type}, mode=mode)
    assert tool._mysql.writes == []
    assert ctx.denials and ctx.denials[0]["tool"] == "pipeline_run"
    assert ui[0]["status"] == "error"
    assert "permission_denied" in result


def test_direct_adapter_cannot_bypass_graph_build_gate(monkeypatch):
    tool = PipelineRunTool()
    tool._mysql = TaskStore()
    with pytest.raises(PermissionError):
        tool.execute(pipeline="L1_build_graph", params="{}", student_id="owner_A")
    assert tool._mysql.writes == []


@pytest.mark.parametrize("pipeline", ["L0_parse_resume", "L2_generate_portrait", "L3_run_match", "L4_generate_report"])
def test_owner_scoped_pipelines_remain_available(monkeypatch, pipeline):
    tool = PipelineRunTool()
    seen = []
    monkeypatch.setattr(tool, "_run_pipeline_sync", lambda name, params: seen.append((name, params)) or "ok")
    ctx = GateContext(interaction_mode=InteractionMode.AGENT, subagent="profile",
                      student_id="owner_A", session_id="session_A")
    gate = pre_tool_use("pipeline_run", {"pipeline": pipeline, "params": '{"student_id":"victim_B"}'}, ctx)
    assert gate.allowed
    assert tool.execute(**gate.args) == "ok"
    assert seen[0][1]["student_id"] == "owner_A"


def test_trusted_domain_runner_is_not_disabled(monkeypatch):
    from app.pipeline.entity_extractor import graph_storage, nsle_pipeline
    from app.services.pipeline import L1BuildGraphPipeline

    writes = []
    monkeypatch.setattr(nsle_pipeline, "NSLEPipeline", lambda **kwargs: SimpleNamespace(
        run_jd_text=lambda text: {"success": True, "required_skills": ["fixture skill"]}))
    monkeypatch.setattr(graph_storage, "GraphStorage", lambda: SimpleNamespace(
        save_job_portrait=lambda **kwargs: writes.append(kwargs) or {"saved_skills": 1}))
    result = L1BuildGraphPipeline("trusted-task", {
        "jd_texts": ["reviewed fixture JD"], "portrait_id": "trusted-portrait",
    }, TaskStore()).run()
    assert result["saved_skills"] == 1
    assert writes[0]["portrait_id"] == "trusted-portrait"


@pytest.mark.skipif(os.environ.get("CPA_TEST_LIVE_PUBLIC_GRAPH") != "1", reason="opt-in Neo4j test")
def test_live_foreign_graph_portrait_is_unchanged(monkeypatch):
    """Real adapter → gate → pipeline → GraphStorage; read back the Neo4j row.

    Before the fix this overwrote the isolated victim sentinel. NSLE output and
    MySQL task tracking are deterministic substitutes; graph writes are real.
    """
    from app.pipeline.entity_extractor import nsle_pipeline
    from app.services.db.neo4j_client import get_neo4j_client

    marker = "agent_gate_" + uuid4().hex
    skill = marker + "_skill"
    neo4j = get_neo4j_client()
    monkeypatch.setattr(nsle_pipeline, "NSLEPipeline", lambda **kwargs: SimpleNamespace(
        run_jd_text=lambda text: {"success": True, "required_skills": [skill]}))
    neo4j.execute_query(
        "CREATE (j:JobPortrait {portrait_id:$id, position_name:'victim baseline', "
        "owner_student_id:'victim_B', test_marker:$id})", {"id": marker}, read_only=False)
    try:
        tool = PipelineRunTool()
        tool._mysql = TaskStore()
        result, ui, ctx = invoke_tool(monkeypatch, tool, {
            "student_id": "victim_B", "portrait_id": marker,
            "position_name": "model supplied overwrite", "jd_texts": ["fixture JD"],
        })
        row = neo4j.execute_query(
            "MATCH (j:JobPortrait {portrait_id:$id}) RETURN j.position_name AS name, "
            "j.owner_student_id AS owner", {"id": marker})[0]
        assert row == {"name": "victim baseline", "owner": "victim_B"}
        assert tool._mysql.writes == []
        assert "permission_denied" in result
    finally:
        neo4j.execute_query("MATCH (j:JobPortrait {portrait_id:$id, test_marker:$id}) DETACH DELETE j",
                            {"id": marker}, read_only=False)
        neo4j.execute_query("MATCH (s:Skill {name:$name}) DETACH DELETE s",
                            {"name": skill}, read_only=False)
