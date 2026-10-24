"""Real ToolNode message path and the public failure boundary."""

import importlib
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.task_packet import TaskPacket


@pytest.mark.parametrize(
    "failure,expected_code,expected_attempts",
    [
        (PermissionError("secret"), "permission_denied", 1),
        (ValueError("bad input"), "tool_execution_error", 1),
        (TimeoutError("timed out"), "tool_timeout", 2),
    ],
)
def test_toolnode_failure_is_structured_and_cannot_finish_as_success(
    monkeypatch, failure, expected_code, expected_attempts,
):
    scoped = importlib.import_module("app.services.agent_runtime.scoped_invoke")
    gated = importlib.import_module("app.services.agent_runtime.gated_tools")
    calls = []
    seen = []

    def lookup(query: str) -> str:
        calls.append(query)
        raise failure

    base = StructuredTool.from_function(func=lookup, name="career_data_read", description="read")
    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=True, args=args, rewritten=False, verdict=SimpleNamespace(reason="ok"),
    ))
    monkeypatch.setattr(gated, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(gated, "get_artifact_committer", lambda: SimpleNamespace(commit_candidates=lambda *_a, **_k: []))
    monkeypatch.setattr(
        scoped, "build_gated_tools",
        lambda _subagent, ctx, collector: [gated._wrap_one(base, ctx, collector)],
    )

    class Model:
        def invoke(self, messages):
            seen.append(list(messages))
            if len(seen) == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "career_data_read", "args": {"query": "x"}, "id": "call-1",
                }])
            return AIMessage(content="已全部完成")

    monkeypatch.setattr("app.services.langgraph_agent._build_llm_with_tools", lambda *_a: Model())
    result = scoped.scoped_invoke(
        TaskPacket(goal="read", subagent="qa", read_keys=[], write_keys=[]),
        Blackboard(), interaction_mode=InteractionMode.AGENT,
    )

    assert len(calls) == expected_attempts
    tool_messages = [m for m in seen[-1] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0].content)
    assert payload["status"] == "error"
    assert payload["error_code"] == expected_code
    assert payload["retryable"] is (expected_code == "tool_timeout")
    assert result["incomplete"] is True
    assert result["stop_reason"] == "tool_failed"
    assert result["tool_calls"][0]["status"] == "error"
    assert result["tool_calls"][0]["error_code"] == expected_code
    assert "未完成" in result["reply"]


def test_transient_read_failure_retries_once_then_settles_as_success(monkeypatch):
    scoped = importlib.import_module("app.services.agent_runtime.scoped_invoke")
    gated = importlib.import_module("app.services.agent_runtime.gated_tools")
    calls = []
    seen = []

    def lookup(query: str) -> str:
        calls.append(query)
        if len(calls) == 1:
            raise TimeoutError("temporary")
        return json.dumps({"status": "done", "success": True, "summary": "read ok"})

    base = StructuredTool.from_function(func=lookup, name="career_data_read", description="read")
    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=True, args=args, rewritten=False, verdict=SimpleNamespace(reason="ok"),
    ))
    monkeypatch.setattr(gated, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(gated, "get_artifact_committer", lambda: SimpleNamespace(commit_candidates=lambda *_a, **_k: []))
    monkeypatch.setattr(
        scoped, "build_gated_tools",
        lambda _subagent, ctx, collector: [gated._wrap_one(base, ctx, collector)],
    )

    class Model:
        def invoke(self, messages):
            seen.append(list(messages))
            if len(seen) == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "career_data_read", "args": {"query": "x"}, "id": "call-1",
                }])
            return AIMessage(content="查询完成")

    monkeypatch.setattr("app.services.langgraph_agent._build_llm_with_tools", lambda *_a: Model())
    result = scoped.scoped_invoke(
        TaskPacket(goal="read", subagent="qa", read_keys=[], write_keys=[]),
        Blackboard(), interaction_mode=InteractionMode.AGENT,
    )
    assert calls == ["x", "x"]
    assert any(isinstance(m, ToolMessage) for m in seen[-1])
    assert result["incomplete"] is False
    assert result["tool_calls"][0]["status"] == "done"
    assert result["tool_calls"][0]["attempts"] == 2


@pytest.mark.parametrize(
    "fail,expected_event_status,expected_incomplete",
    [(False, "succeeded", False), (True, "failed", True)],
)
def test_durable_tool_events_follow_toolnode_and_toolmessage_reinjection(
    monkeypatch, fail, expected_event_status, expected_incomplete,
):
    from app.services.agent_runtime.tool_event_contract import (
        TOOL_SETTLED_EVENT,
        TOOL_STARTED_EVENT,
        ToolEventContext,
    )

    scoped = importlib.import_module("app.services.agent_runtime.scoped_invoke")
    gated = importlib.import_module("app.services.agent_runtime.gated_tools")
    tool_calls = []
    model_messages = []
    persisted_events = []

    def lookup(query: str) -> str:
        tool_calls.append(query)
        if fail:
            raise ValueError("PRIVATE_EXCEPTION_MUST_NOT_PERSIST")
        return json.dumps({
            "status": "done", "success": True,
            "summary": "PRIVATE_RESULT_MUST_NOT_PERSIST",
        })

    base = StructuredTool.from_function(func=lookup, name="career_data_read", description="read")
    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=True, args=args, rewritten=False, verdict=SimpleNamespace(reason="ok"),
    ))
    monkeypatch.setattr(gated, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(gated, "get_artifact_committer", lambda: SimpleNamespace(
        commit_candidates=lambda *_a, **_k: [],
    ))
    monkeypatch.setattr(
        scoped, "build_gated_tools",
        lambda _subagent, ctx, collector: [gated._wrap_one(base, ctx, collector)],
    )

    class Model:
        def invoke(self, messages):
            model_messages.append(list(messages))
            if len(model_messages) == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "career_data_read",
                    "args": {"query": "PRIVATE_QUERY_MUST_NOT_PERSIST"},
                    "id": "call_protocol_01",
                }])
            return AIMessage(content="已依据工具返回继续处理。")

    monkeypatch.setattr("app.services.langgraph_agent._build_llm_with_tools", lambda *_a: Model())
    context = ToolEventContext(
        run_id="run_" + "a" * 32, student_id="synthetic_owner",
        session_id="synthetic_session", claim_token="b" * 32,
        plan_version=2, contract_revision=3, step_index=1, attempt=1,
        allowed_tool_names=frozenset({"career_data_read"}),
    )
    result = scoped.scoped_invoke(
        TaskPacket(goal="只读检索合成信息", subagent="qa", read_keys=[], write_keys=[]),
        Blackboard(), interaction_mode=InteractionMode.AGENT,
        plan_tool_allowlist={"career_data_read"}, allow_artifact_commit=False,
        tool_event_context=context,
        tool_event_sink=lambda event_type, payload: persisted_events.append((event_type, payload)),
    )

    assert tool_calls == ["PRIVATE_QUERY_MUST_NOT_PERSIST"]
    assert len(model_messages) == 2
    reinjected = [message for message in model_messages[1] if isinstance(message, ToolMessage)]
    assert len(reinjected) == 1
    assert reinjected[0].tool_call_id == "call_protocol_01"
    assert [event_type for event_type, _payload in persisted_events] == [
        TOOL_STARTED_EVENT, TOOL_SETTLED_EVENT,
    ]
    started, settled = [payload for _event_type, payload in persisted_events]
    assert started["tool_call_id"] == settled["tool_call_id"] == "call_protocol_01"
    assert started["assistant_message_id"] == settled["assistant_message_id"]
    assert started["input_summary"] == {
        "argument_count": 1, "argument_names": ["query"],
    }
    assert settled["status"] == expected_event_status
    serialized = str(persisted_events)
    assert "PRIVATE_QUERY_MUST_NOT_PERSIST" not in serialized
    assert "PRIVATE_RESULT_MUST_NOT_PERSIST" not in serialized
    assert "PRIVATE_EXCEPTION_MUST_NOT_PERSIST" not in serialized
    assert result["incomplete"] is expected_incomplete


def test_durable_graph_failure_never_retries_through_scoped_fallback(monkeypatch):
    from app.services.agent_runtime import mode_runtime

    packet = TaskPacket(goal="synthetic", subagent="profile", read_keys=[], write_keys=[])
    graph_calls = []
    fallback_calls = []

    def fail_graph(*_args, **_kwargs):
        graph_calls.append(True)
        raise RuntimeError("synthetic graph failure")

    monkeypatch.setattr(mode_runtime, "invoke_subagent_graph", fail_graph)
    monkeypatch.setattr(mode_runtime, "scoped_invoke", lambda *_args, **_kwargs: (
        fallback_calls.append(True) or {"reply": "fallback"}
    ))
    with pytest.raises(RuntimeError, match="synthetic graph failure"):
        mode_runtime._invoke_subagent(
            packet, Blackboard(), "synthetic-session",
            allow_tools=True, interaction_mode=InteractionMode.AGENT,
            student_id="synthetic-owner", plan_tool_allowlist={"career_data_read"},
            tool_event_context=object(), tool_event_sink=lambda *_args: None,
        )
    assert graph_calls == [True]
    assert fallback_calls == []

    # Non-durable legacy callers retain the existing fallback behavior.
    legacy = mode_runtime._invoke_subagent(
        packet, Blackboard(), "synthetic-session",
        allow_tools=True, interaction_mode=InteractionMode.AGENT,
        student_id="synthetic-owner", plan_tool_allowlist={"career_data_read"},
    )
    assert legacy["reply"] == "fallback"
    assert fallback_calls == [True]


def test_tool_start_persistence_failure_prevents_tool_execution(monkeypatch):
    from app.services.agent_runtime.tool_event_contract import ToolEventContext

    scoped = importlib.import_module("app.services.agent_runtime.scoped_invoke")
    gated = importlib.import_module("app.services.agent_runtime.gated_tools")
    executed = []

    def lookup(query: str) -> str:
        executed.append(query)
        return '{"status":"done","success":true}'

    base = StructuredTool.from_function(func=lookup, name="career_data_read", description="read")
    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=True, args=args, rewritten=False, verdict=SimpleNamespace(reason="ok"),
    ))
    monkeypatch.setattr(gated, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(gated, "get_artifact_committer", lambda: SimpleNamespace(
        commit_candidates=lambda *_args, **_kwargs: [],
    ))
    monkeypatch.setattr(
        scoped, "build_gated_tools",
        lambda _subagent, context, collector: [gated._wrap_one(base, context, collector)],
    )

    class FakeLLM:
        def invoke(self, _messages):
            return AIMessage(content="", tool_calls=[{
                "name": "career_data_read", "args": {"query": "synthetic"}, "id": "fail-start-01",
            }])

    monkeypatch.setattr("app.services.langgraph_agent._build_llm_with_tools", lambda *_args: FakeLLM())
    context = ToolEventContext(
        run_id="run_" + "d" * 32, student_id="synthetic-owner",
        session_id="synthetic-session", claim_token="e" * 32,
        plan_version=1, contract_revision=0, step_index=1, attempt=1,
        allowed_tool_names=frozenset({"career_data_read"}),
    )

    def reject_start(event_type, _payload):
        assert event_type == "run.tool_call_started"
        raise RuntimeError("durable start write failed")

    with pytest.raises(RuntimeError, match="durable start write failed"):
        scoped.scoped_invoke(
            TaskPacket(goal="synthetic", subagent="qa", read_keys=[], write_keys=[]),
            Blackboard(), interaction_mode=InteractionMode.AGENT,
            plan_tool_allowlist={"career_data_read"}, allow_artifact_commit=False,
            tool_event_context=context, tool_event_sink=reject_start,
        )
    assert executed == []


def test_unavailable_optional_read_remains_unavailable_without_executor_failure(monkeypatch):
    scoped = importlib.import_module("app.services.agent_runtime.scoped_invoke")
    gated = importlib.import_module("app.services.agent_runtime.gated_tools")
    seen = []
    base = StructuredTool.from_function(
        func=lambda operation: json.dumps({
            "success": False, "status": "unavailable",
            "message": "当前用户没有已确认简历",
        }, ensure_ascii=False),
        name="career_data_read", description="read",
    )
    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=True, args=args, rewritten=False, verdict=SimpleNamespace(reason="ok"),
    ))
    monkeypatch.setattr(gated, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(gated, "get_artifact_committer", lambda: SimpleNamespace(
        commit_candidates=lambda *_a, **_k: [],
    ))
    monkeypatch.setattr(
        scoped, "build_gated_tools",
        lambda _subagent, ctx, collector: [gated._wrap_one(base, ctx, collector)],
    )

    class Model:
        def invoke(self, messages):
            seen.append(list(messages))
            if len(seen) == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "career_data_read", "args": {"operation": "read"},
                    "id": "call-unavailable",
                }])
            return AIMessage(content="资料不可用；以下只根据用户提供的文字回答。")

    monkeypatch.setattr("app.services.langgraph_agent._build_llm_with_tools", lambda *_a: Model())
    result = scoped.scoped_invoke(
        TaskPacket(goal="只读分析用户提供的背景", subagent="qa", read_keys=[], write_keys=[]),
        Blackboard(), interaction_mode=InteractionMode.AGENT,
        plan_tool_allowlist={"career_data_read"}, allow_artifact_commit=False,
    )

    assert result["incomplete"] is False
    assert result["reply"].startswith("资料不可用")
    assert result["tool_calls"][0]["status"] == "unavailable"
    assert "未成功" not in result["tool_calls"][0]["summary"]


def test_tool_result_channel_preserves_unavailable_as_non_crash():
    from app.services.agent_runtime.tool_result_channels import build_ui_view

    card = build_ui_view("career_data_read", {
        "success": False, "status": "unavailable",
        "message": "当前用户没有已确认简历",
    })

    assert card["status"] == "unavailable"
    assert "资料不可用" in card["summary"]
    assert "未成功" not in card["summary"]


def test_permission_gate_is_terminal_and_mutating_tool_is_not_retried(monkeypatch):
    gated = importlib.import_module("app.services.agent_runtime.gated_tools")
    calls = []

    def mutate(query: str) -> str:
        calls.append(query)
        raise TimeoutError("write may have happened")

    base = StructuredTool.from_function(func=mutate, name="pipeline_run", description="write")
    monkeypatch.setattr(gated, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(gated, "get_artifact_committer", lambda: SimpleNamespace(commit_candidates=lambda *_a, **_k: []))
    ctx = gated.make_gate_context(interaction_mode=InteractionMode.AGENT, subagent="qa")
    collector = []
    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=False, args=args, rewritten=False, verdict=SimpleNamespace(reason="denied"),
    ))
    tool = gated._wrap_one(base, ctx, collector)
    denied = json.loads(tool.invoke({"query": "x"}))
    assert denied["error_code"] == "permission_denied"
    assert denied["retryable"] is False
    assert calls == []
    assert collector[-1]["status"] == "error"

    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=True, args=args, rewritten=False, verdict=SimpleNamespace(reason="ok"),
    ))
    tool = gated._wrap_one(base, ctx, collector)
    failed = json.loads(tool.invoke({"query": "x"}))
    assert failed["error_code"] == "tool_timeout"
    assert failed["retryable"] is False
    assert failed["attempts"] == 1
    assert calls == ["x"]


def test_public_json_and_sse_keep_structured_tool_failure_without_raw_exception(monkeypatch):
    from contextlib import nullcontext
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config
    from app.services.agent_runtime.egress_pipeline import project_turn
    from app.services.agent_runtime.tool_result_channels import build_ui_view

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")
    messages = []
    session = SimpleNamespace(student_id="demo_student")
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: SimpleNamespace(
        add_message=lambda **kw: messages.append(kw),
    ))
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", session))
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: SimpleNamespace(lock=lambda _sid: nullcontext()))
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda text, **_kw: {"visible_text": text, "turn_file_ids": [], "parts": [], "mount_errors": []},
    )

    def run_supervisor(_message, session_id, *_args, **_kwargs):
        tool = build_ui_view("career_data_read", {
            "status": "error", "error_code": "tool_timeout", "retryable": True,
            "attempts": 2, "message": "secret backend connection string",
        })
        raw = {"reply": "未完成", "session_id": session_id, "tool_calls": [tool]}
        return project_turn(raw).to_dict(), raw

    monkeypatch.setattr(agent_api, "_run_supervisor", run_supervisor)
    client = create_app().test_client()
    sync = client.post("/api/agent/chat", json={"message": "查资料"})
    assert sync.status_code == 200
    payload = sync.get_json()["data"]
    assert payload["outcome"]["status"] == "partial"
    assert payload["tool_calls"][0]["error_code"] == "tool_timeout"
    assert payload["tool_calls"][0]["retryable"] is True
    assert payload["tool_calls"][0]["attempts"] == 2
    assert "secret backend" not in json.dumps(payload)

    stream = client.post("/api/agent/chat/stream", json={"message": "查资料"})
    events = [json.loads(line[6:]) for line in stream.get_data(as_text=True).splitlines() if line.startswith("data: {")]
    tool_events = [event for event in events if event.get("type") == "tool_done"]
    assert tool_events[0]["tool_call"]["error_code"] == "tool_timeout"
    assert events[-1]["outcome"]["status"] == "partial"
    assert "secret backend" not in stream.get_data(as_text=True)
