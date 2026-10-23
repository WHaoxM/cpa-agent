"""Public graph tool: bounded template lookup through the Agent tool boundary."""

import json

from app.services.agent_runtime.tool_result_channels import build_model_view, build_ui_view
from app.services.tools.graph_query import GraphQueryTool, PUBLIC_QUERIES


class FakeNeo4j:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute_query(self, cypher, parameters=None, read_only=True):
        self.calls.append((cypher, parameters, read_only))
        return self.rows


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _tool(rows, llm_response=None):
    client = FakeNeo4j(rows)
    llm = FakeLLM(llm_response)
    return GraphQueryTool(neo4j_client=client, llm_client=llm), client, llm


def test_query_only_natural_language_selects_public_career_template_and_reaches_model_ui():
    tool, client, llm = _tool(
        [{"role": "前端工程师", "level": "senior"}],
        '{"template":"roles","entity_name":""}',
    )
    raw = tool.execute(query="有哪些职业路径节点？")
    model = build_model_view("graph_query", raw)
    ui = build_ui_view("graph_query", raw)

    assert len(llm.calls) == 1
    assert "不是 Cypher 生成器" in llm.calls[0]["messages"][0]["content"]
    assert client.calls == [(PUBLIC_QUERIES["roles"][0], {}, True)]
    assert "查询结果 (1 条)" in model
    assert 'role="前端工程师"' in model
    assert "未找到匹配的数据" not in model
    assert ui["status"] == "done"
    assert "1 条" in ui["summary"]
    from app.services.agent_runtime.tool_egress import sanitize_tool_call_record
    final_card = sanitize_tool_call_record(ui)
    assert final_card["status"] == "done"
    assert "1 条" in final_card["summary"]


def test_scalar_result_through_agent_tool_adapter_and_gate(monkeypatch):
    from app.services import langgraph_agent
    from app.services.agent_runtime import gated_tools
    from app.services.agent_runtime.modes import InteractionMode

    tool, client, _llm = _tool([{"role": "前端工程师", "level": "senior"}])

    class NoopCommitter:
        def commit_candidates(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(gated_tools, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(gated_tools, "get_artifact_committer", lambda: NoopCommitter())
    previous = langgraph_agent._tool_registry.pop("graph_query", None)
    try:
        adapted = langgraph_agent._adapt_existing_tool(tool)
        ui_events = []
        ctx = gated_tools.make_gate_context(
            interaction_mode=InteractionMode.AGENT,
            subagent="qa",
            student_id="student_test",
            session_id="session_test",
        )
        wrapped = gated_tools._wrap_one(adapted, ctx, ui_events)
        model_output = wrapped.invoke({
            "query": "有哪些公共职业", "query_type": "roles", "entity_name": "",
        })
    finally:
        langgraph_agent._tool_registry.pop("graph_query", None)
        if previous is not None:
            langgraph_agent._tool_registry["graph_query"] = previous

    assert client.calls == [(PUBLIC_QUERIES["roles"][0], {}, True)]
    assert 'role="前端工程师"' in model_output
    assert ui_events[0]["status"] == "done"
    assert "1 条" in ui_events[0]["summary"]
    assert ctx.denials == []


def test_plan_scoped_tool_suppresses_artifact_candidate_commits(monkeypatch):
    from langchain_core.tools import StructuredTool
    from app.services.agent_runtime import gated_tools
    from app.services.agent_runtime.modes import InteractionMode

    commits = []
    audits = []
    monkeypatch.setattr(gated_tools, "enqueue_audit", lambda **kw: audits.append(kw))
    monkeypatch.setattr(gated_tools, "pre_tool_use", lambda _name, args, _ctx: type(
        "Gate", (), {"allowed": True, "args": args, "rewritten": False,
                     "verdict": type("V", (), {"reason": "ok"})()},
    )())
    monkeypatch.setattr(gated_tools, "get_artifact_committer", lambda: type(
        "Committer", (), {"commit_candidates": lambda self, *a, **k: commits.append((a, k))},
    )())
    base = StructuredTool.from_function(
        func=lambda query: json.dumps({
            "success": True, "status": "completed", "summary": "read complete",
            "artifact_candidates": [{"kind": "file", "resource_id": "artifact-x"}],
        }),
        name="career_data_read", description="read",
    )
    context = gated_tools.make_gate_context(
        interaction_mode=InteractionMode.AGENT, subagent="qa",
        student_id="owner_a", session_id="session_a",
        plan_tool_allowlist={"career_data_read"}, allow_artifact_commit=False,
    )
    ui_events = []
    wrapped = gated_tools._wrap_one(base, context, ui_events)

    wrapped.invoke({"query": "read"})

    assert commits == []
    assert audits and audits[0]["tool_name"] == "career_data_read"
    assert "artifact_refs" not in ui_events[0]


def test_person_cypher_cannot_be_submitted_as_template_or_executed():
    tool, client, llm = _tool([{"name": "private student"}])
    output = tool.execute(
        query="MATCH (p:Person) RETURN p.name AS name",
        query_type="MATCH (p:Person) RETURN p",
        entity_name="private student",
    )
    assert "无法确定图谱查询模板" in output
    assert client.calls == []
    assert llm.calls == []


def test_query_only_model_cypher_or_person_template_is_rejected():
    for response in (
        "MATCH (p:Person) RETURN p",
        '{"template":"person","entity_name":"student_001"}',
        '{"template":"roles","entity_name":"","cypher":"MATCH (p:Person) RETURN p"}',
    ):
        tool, client, _ = _tool([], response)
        output = tool.execute(query="查另一位学生的简历实体")
        assert "无法确定图谱查询模板" in output
        assert client.calls == []


def test_missing_required_entity_clarifies_instead_of_false_empty():
    tool, client, _ = _tool([], '{"template":"career_path","entity_name":""}')
    output = tool.execute(query="我有哪些晋升路径？")
    assert "请提供具体技能或岗位名" in output
    assert "未找到匹配的数据" not in output
    assert client.calls == []


def test_explicit_public_template_projects_only_allowlisted_scalars():
    tool, client, _ = _tool([
        {"role": "RAG工程师", "level": "senior", "Person": "student_002"},
    ])
    output = tool.execute(query="列出岗位", query_type="roles")
    assert client.calls == [(PUBLIC_QUERIES["roles"][0], {}, True)]
    assert 'role="RAG工程师"' in json.loads(output)["message"]
    assert json.loads(output)["status"] == "completed"
    assert "student_002" not in output
    assert "Person" not in output


def test_public_templates_do_not_query_unowned_job_portraits_or_person_nodes():
    for cypher, _columns, _needs_entity in PUBLIC_QUERIES.values():
        assert "JobPortrait" not in cypher
        assert "Skill" not in cypher
        assert "CareerNode" in cypher
        assert "NOT" in cypher and ":Person" in cypher
        assert cypher.startswith("MATCH ")
        assert "LIMIT 50" in cypher


def test_career_path_uses_bounded_public_template_and_preview_marks_omissions():
    rows = [{"start": "前端工程师", "target": f"岗位{i}", "steps": 2} for i in range(12)]
    tool, client, _ = _tool(rows)
    output = tool.execute(
        query="前端工程师晋升路径", query_type="career_path", entity_name="前端工程师",
    )
    assert client.calls == [(
        PUBLIC_QUERIES["career_path"][0], {"entity_name": "前端工程师"}, True,
    )]
    assert "steps=2" in output
    assert "查询结果 (12 条)" in output
    assert "还有 2 条记录未展示" in output
    assert json.loads(output)["status"] == "partial"
    assert json.loads(output)["continuation"]
    from app.services.agent_runtime.tool_egress import sanitize_tool_call_record
    card = sanitize_tool_call_record(build_ui_view("graph_query", output))
    assert card["status"] == "partial"
    assert card["truncated"] is True
    assert card["continuation"]


def test_query_only_skill_question_fails_closed_without_touching_database():
    tool, client, _llm = _tool(
        [], '{"template":"skill_unavailable","entity_name":""}',
    )
    output = tool.execute(query="哪些岗位需要 Python？")
    assert "技能图谱查询暂不可用" in output
    assert "不能据此判断没有" in output
    assert client.calls == []

    explicit, explicit_client, _ = _tool([])
    output = explicit.execute(
        query="哪些岗位需要 Python？", query_type="skill_roles", entity_name="Python",
    )
    assert "技能图谱查询暂不可用" in output
    assert explicit_client.calls == []


def test_non_scalar_value_in_public_projection_fails_closed():
    tool, _client, _ = _tool([{"role": {"entity_id": "student_002"}, "level": "senior"}])
    output = tool.execute(query="列出岗位", query_type="roles")
    assert "公共字段投影异常" in output
    assert "student_002" not in output
