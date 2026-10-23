from __future__ import annotations

from datetime import datetime

from app.services.session_manager import SessionManager, SessionState, SessionStatus


def test_rollup_processes_old_history_in_contiguous_batches_and_retains_correction_refs(monkeypatch):
    manager = SessionManager.__new__(SessionManager)
    manager._redis = None
    manager._cache = {}
    session_id = "session_summary_batches"
    messages = [
        {
            "message_id": f"msg_{index}",
            "role": "user" if index % 2 == 0 else "assistant",
            "content": (
                "更正：目标从后端开发改为数据平台工程，旧偏好不再生效。"
                if index == 18 else f"历史消息 {index} " + ("x" * 360)
            ),
        }
        for index in range(100)
    ]
    state = SessionState(
        session_id=session_id,
        student_id="owner-1",
        status=SessionStatus.ACTIVE,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        messages=messages,
    )
    manager._cache[session_id] = state
    manager._save_to_redis = lambda _state: None

    prompts = []

    class FakeLLM:
        def chat(self, messages, **_kwargs):
            prompts.append(messages[0]["content"])
            return (
                "【当前目标】数据平台工程\n【仍有效约束】\n【已确认决定】\n"
                "【未决问题】\n【已被纠正/取代的旧状态】后端开发\n"
                "【相关历史引用】msg_18：更正后的目标"
            )

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())

    manager.maybe_rollup_summary(session_id, keep_messages=16)
    assert state.summary_message_count == 20
    assert '"message_id":"msg_0"' in prompts[0]
    assert '"message_id":"msg_19"' in prompts[0]
    assert '"message_id":"msg_20"' not in prompts[0]
    assert "msg_18" in prompts[0]
    assert '"excerpt_truncated":true' in prompts[0]
    assert "较晚的明确纠正" in prompts[0]
    assert "数据平台工程" in state.conversation_summary
    assert "msg_18" in state.conversation_summary

    manager.maybe_rollup_summary(session_id, keep_messages=16)
    assert state.summary_message_count == 40
    assert '"message_id":"msg_20"' in prompts[1]
    assert '"message_id":"msg_39"' in prompts[1]
    assert '"message_id":"msg_0"' not in prompts[1].split("<ROLLUP_INPUT_JSON>", 1)[1]
    assert "数据平台工程" in prompts[1]
