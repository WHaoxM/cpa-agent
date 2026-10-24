from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime

import pytest

from app.services.session_manager import SessionManager, SessionState, SessionStatus
from app.services.agent_runtime.context_assembler import DefaultContextAssembler
from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.context_assembler import TurnInputs
from app.services.agent_runtime.modes import InteractionMode
from app.services.tools.conversation_history_lookup import create_conversation_history_lookup_tool


def _manager(messages):
    manager = SessionManager.__new__(SessionManager)
    manager._redis = None
    manager._cache = {
        "session-history": SessionState(
            session_id="session-history", student_id="owner-1", status=SessionStatus.ACTIVE,
            created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat(), messages=messages,
        ),
    }
    return manager


def test_history_lookup_searches_old_text_and_reads_bounded_chunks_without_logs(monkeypatch):
    messages = [
        {"message_id": f"msg_{index}", "role": "user" if index % 2 == 0 else "assistant",
         "content": f"ordinary message {index}",
         "metadata": {"tool_calls": [{"secret": "do not expose"}], "thinking_steps": ["private log"]}}
        for index in range(105)
    ]
    messages[4]["content"] = "更正：目标从后端开发改为数据平台工程。"
    messages[4]["metadata"]["parts_v1"] = [{"type": "data_card", "secret": "not history content"}]
    messages[6]["content"] = "L" * 1305
    manager = _manager(messages)
    monkeypatch.setattr("app.services.session_manager.get_session_manager", lambda: manager)

    tool = create_conversation_history_lookup_tool(student_id="owner-1", session_id="session-history")
    search = json.loads(tool.invoke({"action": "search", "query": "数据平台工程"}))
    assert search["status"] == "partial"
    assert search["total_messages"] == 105
    assert search["next_offset"] == 100
    assert search["results"][0]["message_id"] == "msg_4"
    assert "private log" not in json.dumps(search, ensure_ascii=False)
    assert "secret" not in json.dumps(search, ensure_ascii=False)

    first = json.loads(tool.invoke({"action": "read", "message_id": "msg_6", "offset": 0}))
    second = json.loads(tool.invoke({"action": "read", "message_id": "msg_6", "offset": first["next_offset"]}))
    assert first["status"] == "partial" and len(first["content_chunk"]) == 1200
    assert second["status"] == "completed" and second["content_chunk"] == "L" * 105
    assert "metadata" not in first and "tool_calls" not in first


def test_history_lookup_is_owner_session_bound_and_arguments_are_strict(monkeypatch):
    manager = _manager([{"message_id": "msg_1", "role": "user", "content": "private old text"}])
    monkeypatch.setattr("app.services.session_manager.get_session_manager", lambda: manager)
    tool = create_conversation_history_lookup_tool(student_id="owner-2", session_id="session-history")
    result = json.loads(tool.invoke({"action": "read", "message_id": "msg_1"}))
    assert result == {"status": "unavailable", "reason": "message_not_available"}
    with pytest.raises(Exception):
        tool.invoke({"action": "read", "message_id": "msg_1", "student_id": "owner-1"})


def test_mysql_history_fallback_joins_exact_session_owner_and_projects_only_public_text(monkeypatch):
    manager = SessionManager.__new__(SessionManager)
    manager._redis = None
    manager._cache = {}
    manager._load_from_redis = lambda _session_id: None

    class FakeMySQL:
        def __init__(self):
            self.calls = []

        def fetch_all(self, sql, params):
            self.calls.append((sql, params))
            assert "INNER JOIN conversation AS c" in sql
            assert "c.student_id = :student_id" in sql
            if "COUNT(*)" in sql:
                return [{"total_messages": 1}]
            return [{
                "message_id": "msg_db", "role": "assistant", "content": "DB historical answer",
                "thinking_json": '{"thinking_steps":["do not expose"],"parts_v1":[{"type":"data_card"}]}',
                "created_at": "2026-10-23T00:00:00",
            }]

    mysql = FakeMySQL()
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: mysql)
    page = manager.get_owner_scoped_message_page(
        "session-db", "owner-9", offset=0, limit=10, include_components=False,
    )
    assert page["status"] == "completed"
    assert page["messages"][0]["content"] == "DB historical answer"
    assert "parts_v1" not in page["messages"][0]
    assert "thinking_steps" not in page["messages"][0]
    assert mysql.calls[0][1] == {"session_id": "session-db", "student_id": "owner-9"}
    assert mysql.calls[1][1] == {"session_id": "session-db", "student_id": "owner-9", "limit": 10, "offset": 0}


def test_agent_history_reconciles_duplicate_projection_by_id_not_message_text():
    messages = [
        {"message_id": "msg-user-1", "role": "user", "content": "KITE-739 is teal."},
        {"message_id": "msg-assistant-1", "role": "assistant", "content": "I will remember KITE-739."},
        {"message_id": "msg-user-2", "role": "user", "content": "What color was KITE-739?"},
        {"message_id": "msg-assistant-2", "role": "assistant", "content": "It was teal."},
        {"message_id": "msg-user-3", "role": "user", "content": "What color was KITE-739?"},
    ]
    manager = _manager(messages)
    manager._get_messages_from_mysql = lambda *_args, **_kwargs: deepcopy(messages)

    history = manager.get_history_for_agent("session-history", max_messages=16)

    assert [(item["message_id"], item["role"], item["content"]) for item in history] == [
        ("msg-user-1", "user", "KITE-739 is teal."),
        ("msg-assistant-1", "assistant", "I will remember KITE-739."),
        ("msg-user-2", "user", "What color was KITE-739?"),
        ("msg-assistant-2", "assistant", "It was teal."),
        ("msg-user-3", "user", "What color was KITE-739?"),
    ]


def test_agent_history_recovers_older_messages_from_a_partial_cache_by_stable_id():
    cached_suffix = [
        {"message_id": "msg-user-2", "role": "user", "content": "Question two."},
        {"message_id": "msg-assistant-2", "role": "assistant", "content": "Answer two."},
    ]
    manager = _manager(cached_suffix)
    durable_page = [
        {"message_id": "msg-user-1", "role": "user", "content": "Question one."},
        {"message_id": "msg-assistant-1", "role": "assistant", "content": "Answer one."},
        deepcopy(cached_suffix[0]),
    ]
    manager._get_messages_from_mysql = lambda *_args, **_kwargs: durable_page

    history = manager.get_history_for_agent("session-history", max_messages=5)

    assert [(item["message_id"], item["content"]) for item in history] == [
        ("msg-user-1", "Question one."),
        ("msg-assistant-1", "Answer one."),
        ("msg-user-2", "Question two."),
        ("msg-assistant-2", "Answer two."),
    ]


def test_agent_history_prefers_redis_tail_when_mysql_projection_lags():
    cached = [
        {"message_id": "msg-user-1", "role": "user", "content": "First."},
        {"message_id": "msg-assistant-1", "role": "assistant", "content": "Reply."},
        {"message_id": "msg-user-2", "role": "user", "content": "Second."},
        {"message_id": "msg-assistant-2", "role": "assistant", "content": "Latest reply."},
    ]
    manager = _manager(cached)
    manager._get_messages_from_mysql = lambda *_args, **_kwargs: deepcopy(cached[:3])

    history = manager.get_history_for_agent("session-history", max_messages=4)

    assert [item["message_id"] for item in history] == [
        "msg-user-1", "msg-assistant-1", "msg-user-2", "msg-assistant-2",
    ]


def test_agent_history_window_does_not_begin_with_a_detached_assistant_turn():
    manager = _manager([
        {"message_id": "msg-user-1", "role": "user", "content": "First."},
        {"message_id": "msg-assistant-1", "role": "assistant", "content": "Reply one."},
        {"message_id": "msg-user-2", "role": "user", "content": "Second."},
        {"message_id": "msg-assistant-2", "role": "assistant", "content": "Reply two."},
    ])

    history = manager.get_history_for_agent("session-history", max_messages=3)

    assert [item["role"] for item in history] == ["user", "assistant"]
    assert [item["message_id"] for item in history] == ["msg-user-2", "msg-assistant-2"]


def test_history_reconciliation_keeps_redis_when_sources_cannot_prove_interleaving():
    cached = [
        {"message_id": "cached-before-anchor", "role": "user", "content": "cached"},
        {"message_id": "shared-anchor", "role": "assistant", "content": "anchor"},
    ]
    durable = [
        {"message_id": "durable-before-anchor", "role": "user", "content": "durable"},
        {"message_id": "shared-anchor", "role": "assistant", "content": "stale projection"},
    ]
    manager = _manager(cached)
    manager._get_messages_from_mysql = lambda *_args, **_kwargs: deepcopy(durable)

    history = manager.get_history_for_agent("session-history", max_messages=16)

    assert [item["message_id"] for item in history] == [
        "cached-before-anchor", "shared-anchor",
    ]


def test_history_reconciliation_keeps_redis_when_shared_message_order_conflicts():
    cached = [
        {"message_id": "msg-a", "role": "user", "content": "A"},
        {"message_id": "msg-b", "role": "assistant", "content": "B"},
    ]
    durable = [
        deepcopy(cached[1]),
        deepcopy(cached[0]),
    ]
    manager = _manager(cached)
    manager._get_messages_from_mysql = lambda *_args, **_kwargs: durable

    history = manager.get_history_for_agent("session-history", max_messages=16)

    assert [item["message_id"] for item in history] == ["msg-a", "msg-b"]


def test_same_session_history_reaches_ask_model_adapter_in_order_without_private_metadata(monkeypatch):
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    history_rows = [
        {
            "message_id": "synthetic-user-1",
            "role": "user",
            "content": "For this synthetic exercise, KITE-739 is teal.",
            "metadata": {"private_test_marker": "must_not_enter_model_message"},
        },
        {
            "message_id": "synthetic-assistant-1",
            "role": "assistant",
            "content": "Understood: KITE-739 is teal.",
        },
    ]
    manager = _manager(history_rows)
    manager._get_messages_from_mysql = lambda *_args, **_kwargs: deepcopy(history_rows)
    history = manager.get_history_for_agent("session-history", max_messages=16)
    current_message = "What color did I say KITE-739 is?"
    assembled = DefaultContextAssembler().assemble(TurnInputs(
        mode=InteractionMode.ASK,
        message=current_message,
        blackboard=Blackboard(),
        history=history,
    ))

    captured = {}

    class FakeLLM:
        def chat(self, *, messages, **_kwargs):
            captured["messages"] = deepcopy(messages)
            captured["calls"] = captured.get("calls", 0) + 1
            return "synthetic answer"

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(
        "app.services.agent_runtime.scoped_invoke.scoped_invoke",
        lambda *_args, **_kwargs: pytest.fail("synthetic direct-Ask test must not invoke tools"),
    )
    monkeypatch.setattr(mode_runtime, "_ask_public_web_provider_available", lambda: False)
    monkeypatch.setattr(mode_runtime, "_ask_answer_or_repair", lambda *_args, **_kwargs: ([], "Teal."))
    monkeypatch.setattr(
        "app.services.agent_runtime.quality_gate.ground_evidence_answer",
        lambda _llm, answer, *_args, **_kwargs: (answer, {"status": "not_applicable"}),
    )

    ModeRuntime()._run_ask(
        message=current_message,
        blackboard=Blackboard(),
        prefix=assembled.system_head,
        hist=history,
        start=0.0,
        session_id="session-history",
        upgrade_hint=None,
        memory_mount="",
        student_id="synthetic-owner",
        assembled_context=assembled,
        evidence_pack={"coverage": {"reason": "evidence_not_needed"}},
        ask_context={},
    )

    provider_messages = captured["messages"]
    assert captured["calls"] == 1
    assert [item["role"] for item in provider_messages] == [
        "system", "user", "assistant", "user",
    ]
    assert provider_messages[1]["content"] == "For this synthetic exercise, KITE-739 is teal."
    assert provider_messages[-1]["content"] == current_message
    assert sum("KITE-739 is teal" in item["content"] for item in provider_messages) == 2
    assert "must_not_enter_model_message" not in repr(provider_messages)


def test_agent_history_nonpositive_limit_returns_empty_without_mysql_lookup():
    manager = _manager([])

    def fail_mysql_lookup(*_args, **_kwargs):
        raise AssertionError("nonpositive history window must not query MySQL")

    manager._get_messages_from_mysql = fail_mysql_lookup

    assert manager.get_history_for_agent("session-history", max_messages=0) == []


def test_agent_history_mysql_fallback_returns_latest_messages_chronologically(monkeypatch):
    manager = SessionManager.__new__(SessionManager)
    manager._redis = None
    manager._cache = {}
    manager._load_from_redis = lambda _session_id: None

    class FakeMySQL:
        def fetch_all(self, sql, params):
            assert "role IN ('user', 'assistant')" in sql
            assert "ORDER BY id DESC" in sql
            assert params == {"session_id": "session-db-history", "limit": 2, "offset": 0}
            # SQL returns newest first; the service projection restores the
            # chronological order expected by chat completion APIs.
            return [
                {"message_id": "msg-4", "role": "assistant", "content": "latest answer", "created_at": "t4"},
                {"message_id": "msg-3", "role": "user", "content": "latest question", "created_at": "t3"},
            ]

    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: FakeMySQL())

    history = manager.get_history_for_agent("session-db-history", max_messages=2)

    assert [(item["message_id"], item["role"], item["content"]) for item in history] == [
        ("msg-3", "user", "latest question"),
        ("msg-4", "assistant", "latest answer"),
    ]


def test_agent_mysql_pagination_uses_stable_row_order_when_timestamps_match(monkeypatch):
    manager = SessionManager.__new__(SessionManager)

    class FakeMySQL:
        def fetch_all(self, sql, params):
            assert "ORDER BY id DESC" in sql
            assert params == {"session_id": "session-db-history", "limit": 2, "offset": 1}
            return [
                {"message_id": "msg-3", "role": "user", "content": "third", "created_at": "2026-01-01 00:00:00"},
                {"message_id": "msg-2", "role": "assistant", "content": "second", "created_at": "2026-01-01 00:00:00"},
            ]

    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: FakeMySQL())

    page = manager._get_messages_from_mysql("session-db-history", limit=2, offset=1)

    assert [item["message_id"] for item in page] == ["msg-2", "msg-3"]


def test_ask_history_keeps_identical_text_from_distinct_turns_and_current_turn_once():
    manager = DefaultContextAssembler.__new__(DefaultContextAssembler)
    repeated = "请记住这句合成测试文本。"
    history = [
        {"role": "system", "content": "Server system prompt is assembled separately."},
        {"role": "user", "content": repeated},
        {"role": "assistant", "content": "我记住了。"},
        {"role": "assistant", "content": "", "metadata": {"tool_calls": [{"name": "read"}]}},
        {"role": "tool", "content": "internal tool output"},
        {"role": "user", "content": repeated},
    ]

    messages = manager._ask_messages(history, repeated)

    assert messages == [
        {"role": "user", "content": repeated},
        {"role": "assistant", "content": "我记住了。"},
        {"role": "user", "content": repeated},
    ]
    assert all(item["role"] in {"user", "assistant"} for item in messages)
# This suite uses synthetic transcripts and fake provider boundaries only.
