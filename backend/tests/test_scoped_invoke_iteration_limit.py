"""The real LangGraph tool loop must not hide an unexecuted last-step call."""

import importlib

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import StructuredTool

from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.task_packet import TaskPacket
from app.services.agent_runtime.task_packet import build_standard_packet


@pytest.mark.parametrize("final_text", ["", "已完成核验"])
def test_last_iteration_tool_call_is_not_executed_or_reported_as_success(monkeypatch, final_text):
    scoped = importlib.import_module("app.services.agent_runtime.scoped_invoke")
    executions = []
    model_inputs = []

    def lookup(query: str) -> str:
        executions.append(query)
        return f"已核验第 {len(executions)} 项"

    tool = StructuredTool.from_function(
        func=lookup, name="test_lookup", description="查询测试数据"
    )

    class RepeatedToolModel:
        def invoke(self, messages):
            model_inputs.append(list(messages))
            n = len(model_inputs)
            if n == 6 and final_text:
                return AIMessage(content=final_text)
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "test_lookup",
                    "args": {"query": f"step-{n}"},
                    "id": f"call-{n}",
                }],
            )

    monkeypatch.setattr(scoped, "build_gated_tools", lambda *_args: [tool])
    monkeypatch.setattr(
        "app.services.langgraph_agent._build_llm_with_tools",
        lambda *_args: RepeatedToolModel(),
    )

    result = scoped.scoped_invoke(
        TaskPacket(goal="逐项查询后总结", subagent="qa", read_keys=[], write_keys=[]),
        Blackboard(),
        interaction_mode=InteractionMode.AGENT,
    )

    assert len(model_inputs) == 6
    assert executions == [f"step-{i}" for i in range(1, 6)]
    assert "step-6" not in executions
    if final_text:
        assert result["incomplete"] is False
        assert result["stop_reason"] is None
        assert result["reply"] == final_text
    else:
        assert result["incomplete"] is True
        assert result["stop_reason"] == "tool_iteration_limit"
        assert "尚未执行" in result["reply"]
        assert "继续" in result["reply"]
        assert "抱歉，子 Agent 未能生成回复" not in result["reply"]
    assert any(
        isinstance(m, SystemMessage) and "不要再调用工具" in str(m.content)
        for m in model_inputs[-1]
    )


def test_tool_presence_never_commits_business_state():
    from app.services.agent_runtime.plan import run_plan

    packet = build_standard_packet("profile", "生成职业画像")
    for incomplete in (True, False):
        board = Blackboard()
        results, _ = run_plan([packet], board, lambda *args: {
            "reply": "已完成阶段查询",
            "tool_calls": [{"tool_name": "pipeline_run", "status": "done"}],
            "incomplete": incomplete,
        })
        assert not board.has(packet.write_keys[0])
        assert results[0].ok is False
