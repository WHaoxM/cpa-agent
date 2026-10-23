from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.services.session_manager import SessionManager, SessionState, SessionStatus
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
