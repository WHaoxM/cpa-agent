from __future__ import annotations

from datetime import datetime

from app.services.agent_runtime.budget import (
    ModelCapability,
    ask_context_pressure,
    capture_usage_ledger,
    context_window_status,
)
from app.services.agent_runtime.mode_runtime import ModeRuntime
from app.services.session_manager import SessionManager, SessionState, SessionStatus


def test_short_turn_with_large_evidence_and_tool_schema_triggers_from_final_preflight(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent_runtime.budget.model_capability_for",
        lambda provider, model: ModelCapability(
            provider=provider, model=model, context_window_tokens=1600,
            max_output_tokens=600, tokenizer="utf8_bytes_conservative",
            source="test",
        ),
    )
    # One short exchange; the evidence and registered schema dominate the
    # assembled request size. The adapter records the exact final wire parts.
    evidence = "授权资料证据。" * 140
    tool_schema = '{"name":"career_data_read","parameters":{"uri":"career://me/resume"}}'
    with capture_usage_ledger("ask") as ledger:
        status = context_window_status(
            "test", "model",
            [
                {"role": "system", "content": evidence},
                {"role": "system", "content": tool_schema},
                {"role": "user", "content": "总结一下"},
            ],
            max_output_tokens=500,
            safety_margin_tokens=100,
        )

    assert status.estimated_input_tokens is not None
    assert status.input_limit_tokens == 1000
    assert ledger["preflights"][0]["parts"][1]["chars"] == len(tool_schema)
    assert ask_context_pressure(ledger)


def test_low_token_request_does_not_trigger_and_unknown_estimator_is_not_guessed(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent_runtime.budget.model_capability_for",
        lambda provider, model: ModelCapability(
            provider=provider, model=model, context_window_tokens=16000,
            max_output_tokens=2000, tokenizer="utf8_bytes_conservative",
            source="test",
        ),
    )
    with capture_usage_ledger("ask") as ledger:
        context_window_status(
            "test", "model", [{"role": "user", "content": "普通追问"}],
            max_output_tokens=500,
        )
    assert not ask_context_pressure(ledger)

    monkeypatch.setattr(
        "app.services.agent_runtime.budget.model_capability_for",
        lambda provider, model: ModelCapability(
            provider=provider, model=model, context_window_tokens=None,
            tokenizer="unknown", source="test",
        ),
    )
    with capture_usage_ledger("ask") as ledger:
        status = context_window_status(
            "test", "model", [{"role": "user", "content": "无法准确估算"}],
            max_output_tokens=500,
        )
    assert status.fits is None
    assert not ask_context_pressure(ledger)


def test_mode_runtime_routes_ask_compaction_from_actual_preflight(monkeypatch):
    calls = []
    manager = type(
        "FakeManager", (),
        {"maybe_rollup_summary": lambda _self, session_id, keep_messages, *, compact_summary:
         calls.append((session_id, keep_messages, compact_summary))},
    )()
    monkeypatch.setattr("app.services.session_manager.get_session_manager", lambda: manager)
    monkeypatch.setattr(
        "app.services.agent_runtime.budget.model_capability_for",
        lambda provider, model: ModelCapability(
            provider=provider, model=model, context_window_tokens=1200,
            max_output_tokens=600, tokenizer="utf8_bytes_conservative", source="test",
        ),
    )
    runtime = ModeRuntime()

    def fake_chat(_message, **_kwargs):
        context_window_status(
            "test", "model", [{"role": "system", "content": "evidence " + ("x" * 900)}],
            max_output_tokens=500, safety_margin_tokens=100,
        )
        return {"reply": "partial-safe answer"}

    monkeypatch.setattr(runtime, "_chat_impl", fake_chat)
    result = runtime.chat("short question", interaction_mode="ask", session_id="s1")

    assert result["reply"] == "partial-safe answer"
    assert calls == [("s1", 16, True)]


def test_low_pressure_turn_advances_validated_state_without_compacting_summary(monkeypatch):
    manager = SessionManager.__new__(SessionManager)
    manager._redis = None
    manager._cache = {}
    session_id = "session_state_without_compaction"
    messages = [
        {
            "message_id": f"msg_{index}",
            "role": "user" if index % 2 == 0 else "assistant",
            "content": "更正：目标改为数据平台工程。" if index == 18 else f"历史消息 {index}",
        }
        for index in range(36)
    ]
    state = SessionState(
        session_id=session_id, student_id="owner", status=SessionStatus.ACTIVE,
        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat(),
        messages=messages,
    )
    manager._cache[session_id] = state
    manager._save_to_redis = lambda _state: None

    class FakeLLM:
        def chat_json(self, messages, **_kwargs):
            prompt = messages[0]["content"]
            assert "本次仅更新结构化会话状态" in prompt
            return {
                "conversation_state_v1": {
                    "schema_version": 1,
                    "through_message_count": 20,
                    "current_goal": {
                        "text": "目标是数据平台工程",
                        "source_message_id": "msg_18",
                        "source_quote": "更正：目标改为数据平台工程。",
                    },
                    "active_constraints": [],
                    "confirmed_decisions": [],
                    "open_questions": [],
                    "superseded": [],
                },
            }

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    manager.maybe_rollup_summary(session_id, keep_messages=16, compact_summary=False)

    assert state.summary_message_count == 0
    assert state.conversation_summary == ""
    assert state.conversation_state_message_count == 20
    assert state.conversation_state_v1["current_goal"]["source_message_id"] == "msg_18"


def test_summary_failure_keeps_source_cursor_for_retry(monkeypatch):
    manager = SessionManager.__new__(SessionManager)
    manager._redis = None
    manager._cache = {}
    session_id = "session_summary_retry"
    state = SessionState(
        session_id=session_id, student_id="owner", status=SessionStatus.ACTIVE,
        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat(),
        messages=[
            {"message_id": f"msg_{index}", "role": "user" if index % 2 == 0 else "assistant",
             "content": f"消息 {index}"}
            for index in range(40)
        ],
    )
    manager._cache[session_id] = state
    manager._save_to_redis = lambda _state: None

    class FailingLLM:
        def chat(self, *_args, **_kwargs):
            raise RuntimeError("temporary provider error")

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FailingLLM())
    manager.maybe_rollup_summary(session_id, keep_messages=16, compact_summary=True)

    assert state.summary_message_count == 0
    assert state.conversation_summary == ""
    assert state.conversation_state_message_count == 0
