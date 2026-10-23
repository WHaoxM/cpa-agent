from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool


def test_ask_model_can_retry_empty_owner_read_with_public_graph(monkeypatch):
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.gated_tools import _wrap_one
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.permissions import visible_tools_for
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.task_packet import build_standard_packet
    from app.services.agent_runtime.tool_policy import ASK_SEARCH_TOOLS

    executed = []
    first_messages = []

    def career_data_read(operation: str, student_id: str = "") -> str:
        executed.append(("career_data_read", operation, student_id))
        return json.dumps({
            "success": False,
            "status": "unavailable",
            "message": "本人资料暂不可用",
        }, ensure_ascii=False)

    def graph_query(query: str, query_type: str = "roles") -> str:
        executed.append(("graph_query", query_type, query))
        return json.dumps({
            "success": True,
            "status": "completed",
            "row_count": 1,
            "source_id": "public_career_graph",
            "message": "岗位数据：Python 后端开发。来源：public_career_graph。",
        }, ensure_ascii=False)

    base_tools = [
        StructuredTool.from_function(
            func=career_data_read,
            name="career_data_read",
            description="读取当前账号的资料目录。",
        ),
        StructuredTool.from_function(
            func=graph_query,
            name="graph_query",
            description="读取公开岗位图谱。",
        ),
    ]

    def build_test_tools(subagent, gate_ctx, ui_collector):
        visible = visible_tools_for(
            gate_ctx.interaction_mode,
            subagent,
            plan_tool_allowlist=gate_ctx.plan_tool_allowlist,
        )
        return [
            _wrap_one(tool, gate_ctx, ui_collector)
            for tool in base_tools if tool.name in visible
        ]

    class FakeBoundModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            first_messages.append(list(messages))
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "career_data_read",
                    "args": {"operation": "list"},
                    "id": "call-owner-read",
                    "type": "tool_call",
                }])
            if self.calls == 2:
                tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
                assert tool_messages
                assert "unavailable" in str(tool_messages[-1].content)
                return AIMessage(content="", tool_calls=[{
                    "name": "graph_query",
                    "args": {"query": "Python 后端岗位", "query_type": "roles"},
                    "id": "call-public-fallback",
                    "type": "tool_call",
                }])
            tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
            assert any("public_career_graph" in str(m.content) for m in tool_messages)
            return AIMessage(content="公开岗位图谱列出 Python 后端开发；个人资料暂不可用。")

    fake_model = FakeBoundModel()

    import app.services.agent_runtime.scoped_invoke as scoped_module
    import app.services.langgraph_agent as langgraph_module

    monkeypatch.setattr(scoped_module, "build_gated_tools", build_test_tools)
    monkeypatch.setattr(
        langgraph_module,
        "_build_llm_with_tools",
        lambda _tools, _temperature, _max_tokens: fake_model,
    )

    packet = build_standard_packet(
        subagent="qa",
        goal="我的技能是什么？",
        user_utterance="我的技能是什么？",
        success_criteria="区分个人资料不可用与公开岗位图谱证据。",
        write_keys=[],
    )
    out = scoped_invoke(
        packet,
        Blackboard(),
        allow_tools=True,
        interaction_mode=InteractionMode.ASK,
        session_id="session-ask-loop",
        student_id="owner-1",
        plan_tool_allowlist=set(ASK_SEARCH_TOOLS),
        allow_artifact_commit=False,
        ask_context_messages=[
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的答复"},
            {"role": "user", "content": "我的技能是什么？"},
        ],
        ask_context_appendix="只读证据使用规则",
    )

    assert fake_model.calls == 3
    assert executed == [
        ("career_data_read", "list", "owner-1"),
        ("graph_query", "roles", "Python 后端岗位"),
    ]
    system_message = first_messages[0][0]
    assert isinstance(system_message, SystemMessage)
    assert "只读证据使用规则" in str(system_message.content)
    assert [m.content for m in first_messages[0] if isinstance(m, HumanMessage)] == [
        "之前的问题", "我的技能是什么？",
    ]
    assert [m.content for m in first_messages[0] if isinstance(m, AIMessage)] == ["之前的答复"]
    assert "个人资料暂不可用" in out["reply"]
    assert len(out["tool_calls"]) == 2
    assert [call["status"] for call in out["tool_calls"]] == ["unavailable", "done"]
    assert "owner-1" not in json.dumps(out["tool_calls"], ensure_ascii=False)
    assert "只读证据使用规则" not in json.dumps(out["tool_calls"], ensure_ascii=False)
    assert out["incomplete"] is False


def test_ask_can_finish_without_running_a_read_tool(monkeypatch):
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.gated_tools import _wrap_one
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.permissions import visible_tools_for
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.task_packet import build_standard_packet
    from app.services.agent_runtime.tool_policy import ASK_SEARCH_TOOLS

    executed = []
    base = StructuredTool.from_function(
        func=lambda query: executed.append(query) or "should not run",
        name="graph_query",
        description="查询公开岗位图谱。",
    )

    def build_test_tools(subagent, gate_ctx, ui_collector):
        visible = visible_tools_for(
            gate_ctx.interaction_mode,
            subagent,
            plan_tool_allowlist=gate_ctx.plan_tool_allowlist,
        )
        return [_wrap_one(base, gate_ctx, ui_collector)] if base.name in visible else []

    class FakeBoundModel:
        def invoke(self, _messages):
            return AIMessage(content="你好，今天想聊什么？")

    import app.services.agent_runtime.scoped_invoke as scoped_module
    import app.services.langgraph_agent as langgraph_module

    monkeypatch.setattr(scoped_module, "build_gated_tools", build_test_tools)
    monkeypatch.setattr(
        langgraph_module,
        "_build_llm_with_tools",
        lambda _tools, _temperature, _max_tokens: FakeBoundModel(),
    )

    packet = build_standard_packet(
        subagent="qa", goal="你好", user_utterance="你好", write_keys=[],
    )
    out = scoped_invoke(
        packet,
        Blackboard(),
        allow_tools=True,
        interaction_mode=InteractionMode.ASK,
        plan_tool_allowlist=set(ASK_SEARCH_TOOLS),
        allow_artifact_commit=False,
    )

    assert out["reply"] == "你好，今天想聊什么？"
    assert executed == []
    assert out["tool_calls"] == []


def test_ask_loads_only_released_school_skill_on_demand(monkeypatch):
    import time
    from types import SimpleNamespace

    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.mode_runtime import ModeRuntime, _ask_skill_ids

    assert _ask_skill_ids("我這所學校是 211 嗎？") == ["school-context"]
    assert _ask_skill_ids("你好，今天聊什麼？") == []
    assert _ask_skill_ids("招聘要求硕士和博士吗？") == []

    class FakeLLM:
        def chat(self, **_kwargs):
            return "院校背景的具体判断需要以已核对的来源为准。"

    calls = []

    def fake_scoped_invoke(packet, _blackboard, **kwargs):
        calls.append((packet, kwargs))
        return {
            "reply": "院校背景的具体判断需要以已核对的来源为准。",
            "tool_calls": [{
                "tool_name": "school_context_source_read",
                "status": "partial",
                "summary": "院校背景来源未核对",
            }],
            "incomplete": False,
            "skill_versions": [{"skill_id": "school-context", "version": "1.0.2"}],
        }

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    monkeypatch.setattr("app.services.agent_runtime.scoped_invoke.scoped_invoke", fake_scoped_invoke)

    question = "我這所學校是 211 嗎？"
    assembled = SimpleNamespace(
        system_prompt="服务端系统上下文",
        messages=[{"role": "user", "content": question}],
        system_tail="",
    )
    result = ModeRuntime()._run_ask(
        question, Blackboard(), "服务端系统上下文", [], time.time(), "session_ask_skill",
        None, "", student_id="owner-1", turn_file_ids=[], mount_errors=[],
        assembled_context=assembled,
        evidence_pack={
            "status": "empty", "items": [], "sources": [],
            "coverage": {"reason": "evidence_not_needed"},
        },
    )

    assert len(calls) == 1
    packet, options = calls[0]
    assert packet.skill_ids == ["school-context"]
    assert packet.write_keys == []
    assert options["allow_tools"] is False
    assert options["interaction_mode"].value == "ask"
    assert result["orchestration"]["supervisor_view"]["path"] == "ask_professional_skill"
    assert result["orchestration"]["supervisor_view"]["skill_versions"][0]["version"] == "1.0.2"
    assert "school_context_source_read" not in result["reply"]
