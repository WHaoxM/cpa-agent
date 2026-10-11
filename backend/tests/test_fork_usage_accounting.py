"""Real rollup -> LLMClient -> SQL ledger chain, synthetic provider only."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from app.services.agent_runtime import fork_jobs, fork_usage_store
from app.services.agent_runtime.budget import capture_usage_ledger


@pytest.fixture
def rollup(monkeypatch, tmp_path):
    from app.services import session_manager
    from app.utils import llm_client

    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    fork_usage_store.install_fork_usage_schema(engine)
    monkeypatch.setattr(fork_usage_store, "_engine", lambda: engine)
    monkeypatch.setattr(fork_jobs, "_run_inline_for_tests", True)
    state = SimpleNamespace(messages=[{"role": "user", "content": "hello"}] * 18,
        conversation_summary="original", summary_message_count=0, updated_at="")
    manager = object.__new__(session_manager.SessionManager)
    manager._cache = {}
    manager.get_session = lambda _sid: state
    manager._save_to_redis = lambda _state: None
    monkeypatch.setattr(session_manager, "get_session_manager", lambda: manager)
    client = llm_client.LLMClient(provider="openai", api_key="test-only", model="gpt-4o-mini")
    attempts = []
    response_options = {"finish_reason": "stop", "usage": SimpleNamespace(
        prompt_tokens=19, completion_tokens=7, total_tokens=26)}
    def dispatch(**kwargs):
        attempts.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="summary"), finish_reason=response_options["finish_reason"])],
            usage=response_options["usage"])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=dispatch)))
    monkeypatch.setattr(client, "_get_openai", lambda provider: (fake, client._resolve_config("openai")))
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: client)
    return state, attempts, response_options


def run_rollup():
    with capture_usage_ledger("experts", session_id="session", student_id="owner") as parent:
        fork_jobs.enqueue("session", "rollup_summary", {"keep_messages": 16})
    reference = parent["background_jobs"][0]
    query = dict(job_id=reference["job_id"], session_id="session", student_id="owner",
                 usage_turn_id=parent["usage_turn_id"])
    return parent, query, fork_usage_store.read_record(**query)


def test_rollup_budget_partial_preserves_cursor_and_next_turn_retries(rollup, monkeypatch):
    state, attempts, _ = rollup
    monkeypatch.setenv("CPA_AGENT_FORK_TOKEN_CAP", "10")
    parent, query, record = run_rollup()
    assert record["status"] == "partial"
    assert record["ledger"]["stop_reason"] == "turn_token_cap_exceeded"
    assert record["ledger"]["resume_state"] == "retry_on_next_turn"
    assert record["ledger"]["origin_usage_turn_id"] == parent["usage_turn_id"]
    assert attempts == []
    assert state.summary_message_count == 0
    assert state.conversation_summary == "original"
    assert parent["calls"] == []  # fork usage is never mischarged to parent
    assert fork_usage_store.read_record(**{**query, "student_id": "other"}) is None
    assert fork_usage_store.read_record(**{**query, "usage_turn_id": "other"}) is None
    monkeypatch.setenv("CPA_AGENT_FORK_TOKEN_CAP", "3000")
    _, _, completed = run_rollup()
    assert completed["status"] == "completed"
    assert completed["ledger"]["calls"][0]["usage"]["total_tokens"] == 26
    assert len(attempts) == 1
    assert state.summary_message_count == 2
    assert state.conversation_summary == "summary"


def test_missing_accounting_store_blocks_rollup_dispatch(rollup, monkeypatch):
    def fail(_job):
        raise OSError("store down")
    monkeypatch.setattr(fork_usage_store, "create_record", fail)
    with capture_usage_ledger("agent", session_id="session", student_id="owner") as parent:
        fork_jobs.enqueue("session", "rollup_summary")
    assert parent["background_jobs"][0]["status"] == "accounting_unavailable"
    assert rollup[1] == []


@pytest.mark.parametrize("change,reason", [
    ({"usage": None}, "turn_token_usage_unverified"),
    ({"finish_reason": "length"}, "model_output_truncated"),
])
def test_incomplete_provider_result_never_advances_summary_cursor(rollup, monkeypatch, change, reason):
    state, attempts, options = rollup
    options.update(change)
    monkeypatch.setenv("CPA_AGENT_FORK_TOKEN_CAP", "3000")
    _, _, record = run_rollup()
    assert record["status"] == "partial"
    assert record["ledger"]["stop_reason"] == reason
    assert state.summary_message_count == 0
    assert state.conversation_summary == "original"
    assert len(attempts) == 1
