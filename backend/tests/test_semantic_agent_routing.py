"""Contract tests for the live ModeRuntime route, not a detached classifier."""

from app.services.agent_runtime.mode_runtime import ModeRuntime, _llm_route


def test_invalid_router_output_is_not_a_business_command(monkeypatch):
    class InvalidLLM:
        def chat_json(self, **_kwargs):
            return {"action": "call", "subagent": "report_everything", "goal": "publish"}

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: InvalidLLM())
    assert _llm_route("我要一份报告", "") is None


def test_router_failure_falls_through_to_normal_qa_with_tools(monkeypatch):
    calls = []

    def invoke(packet, _blackboard, _session_id, **kwargs):
        calls.append((packet.subagent, kwargs["allow_tools"]))
        return {"reply": "先确认你的目标岗位，再分析合适程度。", "tool_calls": []}

    monkeypatch.setattr("app.services.agent_runtime.mode_runtime._llm_route", lambda *_: None)
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime._invoke_subagent", invoke)
    result = ModeRuntime().chat("帮我推荐适合的岗位", interaction_mode="agent")
    assert calls == [("qa", True)]
    assert result["orchestration"]["strategy"] == "agent"
    assert result["active_subagent"] == "qa_subagent"
    assert "目标岗位" in result["reply"]


def test_ambiguous_request_can_clarify_without_keyword_guess(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent_runtime.mode_runtime._llm_route",
        lambda *_: {"action": "clarify", "clarify_question": "你希望先分析简历还是查询岗位？"},
    )
    result = ModeRuntime().chat("帮我看看", interaction_mode="agent")
    assert result["orchestration"]["strategy"] == "agent_clarify"
    assert "先分析简历" in result["reply"]
