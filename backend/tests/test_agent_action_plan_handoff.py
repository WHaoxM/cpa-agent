"""The browser can select an opaque offer, but cannot supply its sources."""

import pytest

from app.api.agent import _prepare_and_record_user_turn


class RecordingSessionManager:
    def __init__(self):
        self.messages = []

    def add_message(self, **kwargs):
        self.messages.append(kwargs)


def _prepared(*_args, **kwargs):
    incoming = next(
        item for item in kwargs.get("content_parts", [])
        if isinstance(item, dict) and item.get("type") == "action_plan_handoff"
    )
    return {
        "visible_text": "把这个差距变成计划",
        "parts": [
            {"type": "text", "text": "把这个差距变成计划"},
            {"type": "action_plan_handoff", "handoff_id": incoming.get("handoff_id"),
             "action": incoming.get("action")},
        ],
        "turn_file_ids": [], "mount_errors": [], "voice_deduped": False,
    }


def test_handoff_command_uses_session_context_and_rejects_client_chosen_binding(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn", _prepared,
    )
    context = {
        "action_plan_handoff": {
            "handoff_id": "aph_" + "a" * 32,
            "phase": "offered",
            "report_uri": "career://me/reports/report_server_bound",
            "report_version": "report_v1",
            "resume_version": "resume_v1",
            "gap_summary": "报告指出具体缺口。",
        },
    }
    manager = RecordingSessionManager()
    _message, prepared = _prepare_and_record_user_turn(
        manager, session_id="sess_1", student_id="owner_1", message="",
        attachment_ids=[], content_parts=[{
            "type": "action_plan_handoff", "handoff_id": "aph_" + "a" * 32,
            "action": "start", "report_uri": "career://me/reports/forged",
            "student_id": "owner_2",
        }], voice_turn_id=None, session_context=context,
    )
    assert prepared["action_plan_command"]["context"]["report_uri"] == context["action_plan_handoff"]["report_uri"]
    assert manager.messages[0]["metadata"]["content_parts"][-1]["type"] == "action_plan_handoff"

    manager = RecordingSessionManager()
    with pytest.raises(LookupError):
        _prepare_and_record_user_turn(
            manager, session_id="sess_1", student_id="owner_1", message="",
            attachment_ids=[], content_parts=[{
                "type": "action_plan_handoff", "handoff_id": "aph_" + "b" * 32,
                "action": "start",
            }], voice_turn_id=None, session_context=context,
        )
    assert manager.messages == []
