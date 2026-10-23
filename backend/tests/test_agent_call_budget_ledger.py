"""Provider-boundary budget checks and request-local usage capture, no network."""

from types import SimpleNamespace
import os

import pytest

from app.services.agent_runtime.budget import (
    ContextWindowStatus,
    capture_usage_ledger,
    require_turn_token_capacity,
    reserve_tool_call_capacity,
)


@pytest.fixture
def offline_budget_estimator(monkeypatch):
    """Inject the existing deterministic UTF-8 estimator into provider-boundary tests.

    These tests verify reservation/accounting decisions, not OpenAI tokenizer
    parity. Keeping the capability injection local avoids downloading tiktoken
    and leaves production model capability selection unchanged.
    """
    from app.services.agent_runtime import budget

    monkeypatch.setenv("CPA_LLM_CONTEXT_WINDOW_TOKENS_OPENAI", "16384")

    def deterministic_capability(provider, model):
        configured_window = os.getenv(f"CPA_LLM_CONTEXT_WINDOW_TOKENS_{provider.upper()}", "16384")
        return budget.ModelCapability(
            provider=provider,
            model=model,
            context_window_tokens=int(configured_window),
            tokenizer="utf8_bytes_conservative",
            source="offline_test_injection",
        )

    monkeypatch.setattr(budget, "model_capability_for", deterministic_capability)


def test_turn_cap_reserves_input_and_output_before_dispatch():
    with capture_usage_ledger("experts", turn_token_cap=100) as ledger:
        with pytest.raises(RuntimeError, match="turn_token_cap_exceeded"):
            require_turn_token_capacity(70, estimated_input_tokens=31)
    assert ledger["calls"] == []


@pytest.mark.parametrize(
    "kwargs", [
        {"requested_output_tokens": -1},
        {"requested_output_tokens": 1, "estimated_input_tokens": -1},
        {"requested_output_tokens": True},
    ],
)
def test_model_budget_rejects_invalid_token_reservation(kwargs):
    with capture_usage_ledger("agent") as ledger:
        with pytest.raises(ValueError):
            require_turn_token_capacity(**kwargs)
    assert ledger["model_calls_reserved"] == 0


def test_tool_call_cap_reserves_batches_before_any_tool_execution():
    with capture_usage_ledger("agent", tool_call_cap=2) as ledger:
        reserve_tool_call_capacity(2, stage="fixture_batch")
        with pytest.raises(RuntimeError, match="tool_call_cap_exceeded"):
            reserve_tool_call_capacity(1, stage="fixture_retry")

    assert ledger["tool_calls_reserved"] == 2
    assert ledger["stop_reason"] == "tool_call_cap_exceeded"
    assert ledger["budget_stop"] == {
        "reason": "tool_call_cap_exceeded",
        "reserved_tool_calls": 2,
        "requested_tool_calls": 1,
        "limit_tool_calls": 2,
        "stage": "fixture_retry",
    }


def test_model_call_cap_reserves_before_dispatch_and_settles_without_false_stop():
    status = ContextWindowStatus(
        provider="synthetic", model="fixture", context_window_tokens=4096,
        input_limit_tokens=3000, estimated_input_tokens=10,
        remaining_input_tokens=2990, requested_output_tokens=50,
        fits=True, estimate_method="fixture", reason="fits_estimate",
    )
    with capture_usage_ledger("agent", model_call_cap=1) as ledger:
        require_turn_token_capacity(50, estimated_input_tokens=10)
        assert ledger["model_calls_reserved"] == 1
        with pytest.raises(RuntimeError, match="model_call_cap_exceeded"):
            require_turn_token_capacity(50, estimated_input_tokens=10)

        # The failed second preflight sets a stop reason; a fresh one-call
        # ledger verifies that settlement at the cap is not treated as a new
        # model attempt.
    with capture_usage_ledger("agent", model_call_cap=1) as settled:
        require_turn_token_capacity(50, estimated_input_tokens=10)
        from app.services.agent_runtime.budget import record_model_call

        record_model_call(
            provider="synthetic", model="fixture", stage="fixture",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=status, finish_reason="stop",
        )
        require_turn_token_capacity(0)
        assert settled["model_calls_reserved"] == 0
        assert settled["model_token_reservations"] == []
        assert len(settled["calls"]) == 1


def test_durable_usage_offsets_apply_to_model_and_tool_calls():
    with capture_usage_ledger(
        "agent_run_step", model_call_cap=3, tool_call_cap=4,
        model_calls_used=2, tool_calls_used=3,
    ) as ledger:
        require_turn_token_capacity(1, estimated_input_tokens=0)
        assert ledger["model_calls_reserved"] == 1
        with pytest.raises(RuntimeError, match="model_call_cap_exceeded"):
            require_turn_token_capacity(1, estimated_input_tokens=0)
        assert ledger["model_calls_reserved"] == 1
    with capture_usage_ledger(
        "agent_run_step", tool_call_cap=4, tool_calls_used=3,
    ) as ledger:
        assert ledger["tool_calls_reserved"] == 3
        with pytest.raises(RuntimeError, match="tool_call_cap_exceeded"):
            reserve_tool_call_capacity(2, stage="durable_step")
        assert ledger["tool_calls_reserved"] == 3


def test_llm_client_model_call_cap_blocks_provider_before_dispatch(monkeypatch):
    import app.utils.llm_client as llm_module
    from app.utils.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="fixture", model="gpt-4o-mini")
    status = ContextWindowStatus(
        provider="openai", model="gpt-4o-mini", context_window_tokens=4096,
        input_limit_tokens=3000, estimated_input_tokens=10,
        remaining_input_tokens=2990, requested_output_tokens=20,
        fits=True, estimate_method="fixture", reason="fits_estimate",
    )
    monkeypatch.setattr(llm_module, "context_window_status", lambda *_args: status)
    dispatched = []

    def chat_once(*_args, **_kwargs):
        dispatched.append(1)
        return (
            "ok", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "gpt-4o-mini", "stop",
        )

    monkeypatch.setattr(client, "_chat_once", chat_once)
    with capture_usage_ledger("agent_run_step", model_call_cap=1) as ledger:
        client.chat([{"role": "user", "content": "hello"}], max_tokens=20)
        with pytest.raises(RuntimeError, match="model_call_cap_exceeded"):
            client.chat([{"role": "user", "content": "again"}], max_tokens=20)

    assert dispatched == [1]
    assert len(ledger["calls"]) == 1
    assert ledger["model_calls_reserved"] == 0


def test_scoped_tool_batch_over_cap_is_rejected_before_execution(monkeypatch):
    import json

    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool

    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.task_packet import build_standard_packet
    from app.services.agent_runtime.tool_policy import ASK_SEARCH_TOOLS
    import app.services.agent_runtime.scoped_invoke as scoped_module
    import app.services.langgraph_agent as langgraph_module

    executed = []

    def career_data_read(operation: str, student_id: str = "") -> str:
        executed.append((operation, student_id))
        return json.dumps({"status": "completed"})

    base_tools = [StructuredTool.from_function(
        func=career_data_read,
        name="career_data_read",
        description="Read the current owner's profile.",
    )]

    def build_test_tools(subagent, gate_ctx, ui_collector):
        from app.services.agent_runtime.gated_tools import _wrap_one

        visible = {
            tool.name for tool in base_tools
            if tool.name in gate_ctx.plan_tool_allowlist
        }
        return [_wrap_one(tool, gate_ctx, ui_collector)
                for tool in base_tools if tool.name in visible]

    class FakeBoundModel:
        def invoke(self, _messages):
            return AIMessage(content="", tool_calls=[
                {"name": "career_data_read", "args": {"operation": "list"},
                 "id": "first", "type": "tool_call"},
                {"name": "career_data_read", "args": {"operation": "list"},
                 "id": "second", "type": "tool_call"},
            ])

    monkeypatch.setattr(scoped_module, "build_gated_tools", build_test_tools)
    monkeypatch.setattr(langgraph_module, "_build_llm_with_tools", lambda *_args: FakeBoundModel())
    packet = build_standard_packet(
        subagent="qa", goal="Read profile", user_utterance="Read profile", write_keys=[],
    )

    with capture_usage_ledger("ask", tool_call_cap=1) as ledger:
        result = scoped_invoke(
            packet,
            Blackboard(),
            allow_tools=True,
            interaction_mode=InteractionMode.ASK,
            session_id="session-tool-cap",
            student_id="owner-tool-cap",
            plan_tool_allowlist={"career_data_read"},
            allow_artifact_commit=False,
        )

    assert executed == []
    assert result["incomplete"] is True
    assert result["stop_reason"] == "tool_call_cap_exceeded"
    assert ledger["tool_calls_reserved"] == 0


def test_public_turn_contains_usage_without_private_provider_metadata():
    from app.services.agent_runtime.egress_pipeline import project_turn

    turn = project_turn({"reply": "ok", "usage_ledger": {
        "mode": "experts", "turn_token_cap": 100,
        "calls": [{"call_id": "lead:1", "stage": "lead", "provider": "qwen",
                   "model": "qwen-plus", "usage": {"prompt_tokens": 5,
                   "completion_tokens": 1, "total_tokens": 6, "secret": "hidden"},
                   "prompt": "private"}],
    }}).to_dict()
    assert turn["usage_ledger"]["verified"] is True
    assert turn["usage_ledger"]["calls"][0]["usage"]["total_tokens"] == 6
    assert "secret" not in str(turn["usage_ledger"])
    assert "private" not in str(turn["usage_ledger"])


def test_failed_provider_dispatch_is_unverified_not_free(monkeypatch, offline_budget_estimator):
    from app.utils.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    attempts = []

    def failed(**_kwargs):
        attempts.append(1)
        raise TimeoutError("provider timeout")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=failed)))
    monkeypatch.setattr(client, "_get_openai", lambda _provider: (fake, client._resolve_config("openai")))
    with capture_usage_ledger("experts", turn_token_cap=300) as ledger:
        with pytest.raises(RuntimeError, match="turn_token_usage_unverified"):
            client.chat([{"role": "user", "content": "hello"}], max_tokens=50)
    assert attempts == [1]
    assert ledger["calls"][0]["usage"] is None
    assert ledger["calls"][0]["finish_reason"] == "provider_error"


def test_fork_job_has_independent_budget_capture(monkeypatch):
    from app.services.agent_runtime import fork_jobs
    from app.services.agent_runtime.budget import current_usage_ledger

    observed = []
    monkeypatch.setitem(fork_jobs._handlers, "budget_fixture", lambda _job: observed.append(current_usage_ledger()))
    fork_jobs._run_job(fork_jobs.ForkJob(kind="budget_fixture", session_id="fixture"))
    assert len(observed) == 1
    assert observed[0]["mode"] == "fork:budget_fixture"
    assert current_usage_ledger() is None


def test_langgraph_unknown_window_rejected_before_provider(monkeypatch):
    from app.services import langgraph_agent
    from config import integration_loader

    calls = []

    class Candidate:
        def __init__(self, **_kwargs):
            pass

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages, **_kwargs):
            calls.append(messages)
            return "must not be called"

    monkeypatch.setattr(langgraph_agent, "ChatOpenAI", Candidate)
    monkeypatch.setattr(integration_loader, "get_failover_order", lambda: ["synthetic"])
    monkeypatch.setattr(integration_loader, "get_provider_config", lambda _p: {
        "api_key": "fixture", "base_url": "https://example.invalid", "model": "unlisted-unknown-window",
    })
    bound = langgraph_agent._build_llm_with_tools([], 0, 128)
    with pytest.raises(RuntimeError, match="model_capability_unknown"):
        bound.invoke([{"role": "user", "content": "final-tool-round" * 10000}])
    assert calls == []


def test_langgraph_records_each_final_round_and_rejects_turn_cap(monkeypatch, offline_budget_estimator):
    from langchain_core.messages import AIMessage, HumanMessage
    from app.services import langgraph_agent
    from config import integration_loader

    calls = []

    class Candidate:
        def __init__(self, **_kwargs):
            pass

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages, **_kwargs):
            calls.append(messages)
            return AIMessage(
                content="answer", usage_metadata={"input_tokens": 11, "output_tokens": 4, "total_tokens": 15},
                response_metadata={"finish_reason": "stop"},
            )

    monkeypatch.setattr(langgraph_agent, "ChatOpenAI", Candidate)
    monkeypatch.setattr(integration_loader, "get_failover_order", lambda: ["openai"])
    monkeypatch.setattr(integration_loader, "get_provider_config", lambda _p: {
        "api_key": "fixture", "base_url": "https://example.invalid", "model": "gpt-4o-mini",
    })
    # Keep reservations viable under the deliberately pessimistic UTF-8-byte
    # test estimator; the test still exercises two calls and a pre-dispatch
    # stop at the selected turn cap.
    bound = langgraph_agent._build_llm_with_tools([], 0, 64)
    with capture_usage_ledger("agent", turn_token_cap=200) as ledger:
        bound.invoke([HumanMessage(content="first")])
        bound.invoke([HumanMessage(content="second")])
    assert len(calls) == 2
    assert ledger["expected_call_ids"] == ["langgraph:1", "langgraph:2"]
    assert [c["usage"]["total_tokens"] for c in ledger["calls"]] == [15, 15]
    assert all(c["preflight"]["fits"] is True for c in ledger["calls"])

    with capture_usage_ledger("agent", turn_token_cap=150) as ledger:
        bound.invoke([HumanMessage(content="first")])
        with pytest.raises(RuntimeError, match="turn_token_cap_exceeded"):
            bound.invoke([HumanMessage(content="second")])
    assert len(calls) == 3
    assert len(ledger["calls"]) == 1


def test_llm_client_records_provider_usage_without_last_call_loss(monkeypatch, offline_budget_estimator):
    from app.utils.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="answer"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=19, completion_tokens=7, total_tokens=26),
        model="gpt-4o-mini",
    )
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response)))
    monkeypatch.setattr(client, "_get_openai", lambda _provider: (fake, client._resolve_config("openai")))
    with capture_usage_ledger("ask", turn_token_cap=250) as ledger:
        client.chat([{"role": "user", "content": "hello"}], max_tokens=100)
        client.chat([{"role": "user", "content": "again"}], max_tokens=100)
    assert [c["usage"]["total_tokens"] for c in ledger["calls"]] == [26, 26]
    assert ledger["expected_call_ids"] == ["llm_client.chat:1", "llm_client.chat:2"]


def test_mode_runtime_returns_request_local_ledger(monkeypatch, offline_budget_estimator):
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.budget import record_model_call, context_window_status

    def fake_chat(_self, _message, **_kwargs):
        status = context_window_status("openai", "gpt-4o-mini", [
            {"role": "user", "content": "sent"},
        ], 100)
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="fixture",
            usage={"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
            preflight=status, finish_reason="stop",
        )
        return {"reply": "done"}

    monkeypatch.setattr(ModeRuntime, "_chat_impl", fake_chat)
    first = ModeRuntime().chat("hi", interaction_mode="ask")
    second = ModeRuntime().chat("hi", interaction_mode="agent")
    assert first["usage_ledger"]["mode"] == "ask"
    assert second["usage_ledger"]["mode"] == "agent"
    assert first["usage_ledger"]["calls"][0]["usage"]["total_tokens"] == 15
    assert len(second["usage_ledger"]["calls"]) == 1


def test_missing_or_over_cap_provider_usage_cannot_continue(monkeypatch, offline_budget_estimator):
    from app.utils.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="answer"), finish_reason="stop")],
        usage=None, model="gpt-4o-mini",
    )
    calls = []

    def create(**_kwargs):
        calls.append(1)
        return response

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(client, "_get_openai", lambda _provider: (fake, client._resolve_config("openai")))
    with capture_usage_ledger("ask", turn_token_cap=200) as ledger:
        with pytest.raises(RuntimeError, match="turn_token_usage_unverified"):
            client.chat([{"role": "user", "content": "hello"}], max_tokens=100)
    assert len(calls) == 1
    assert ledger["calls"][0]["usage"] is None

    response.usage = SimpleNamespace(prompt_tokens=120, completion_tokens=100, total_tokens=220)
    with capture_usage_ledger("ask", turn_token_cap=200) as ledger:
        with pytest.raises(RuntimeError, match="turn_token_cap_exceeded"):
            client.chat([{"role": "user", "content": "hello"}], max_tokens=100)
    assert len(calls) == 2
    assert ledger["calls"][0]["usage"]["total_tokens"] == 220


def test_langgraph_real_tool_schema_is_in_final_preflight(monkeypatch, offline_budget_estimator):
    from langchain_core.tools import tool
    from app.services import langgraph_agent
    from config import integration_loader

    calls = []

    class Candidate:
        def __init__(self, **_kwargs):
            pass

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages, **_kwargs):
            calls.append(messages)
            return "unexpected"

    @tool(description="Evidence-backed lookup. " * 350)
    def evidence_lookup(query: str) -> str:
        return query

    monkeypatch.setenv("CPA_LLM_CONTEXT_WINDOW_TOKENS_OPENAI", "1024")
    monkeypatch.setattr(langgraph_agent, "ChatOpenAI", Candidate)
    monkeypatch.setattr(integration_loader, "get_failover_order", lambda: ["openai"])
    monkeypatch.setattr(integration_loader, "get_provider_config", lambda _p: {
        "api_key": "fixture", "base_url": "https://example.invalid", "model": "gpt-4o-mini",
    })
    bound = langgraph_agent._build_llm_with_tools([evidence_lookup], 0, 128)
    with pytest.raises(RuntimeError, match="estimated_input_over_window"):
        bound.invoke([{"role": "user", "content": "hi"}])
    assert calls == []


def test_langgraph_unknown_usage_is_not_verified(monkeypatch, offline_budget_estimator):
    from langchain_core.messages import AIMessage
    from app.services import langgraph_agent
    from config import integration_loader

    class Candidate:
        def __init__(self, **_kwargs):
            pass

        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages, **_kwargs):
            return AIMessage(content="answer", response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(langgraph_agent, "ChatOpenAI", Candidate)
    monkeypatch.setattr(integration_loader, "get_failover_order", lambda: ["openai"])
    monkeypatch.setattr(integration_loader, "get_provider_config", lambda _p: {
        "api_key": "fixture", "base_url": "https://example.invalid", "model": "gpt-4o-mini",
    })
    bound = langgraph_agent._build_llm_with_tools([], 0, 128)
    with capture_usage_ledger("agent") as ledger:
        with pytest.raises(RuntimeError, match="turn_token_usage_unverified"):
            bound.invoke([{"role": "user", "content": "hi"}])
    assert ledger["calls"][0]["usage"] is None
    assert ledger["expected_call_ids"] == ["langgraph:1"]
