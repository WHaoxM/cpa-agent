from __future__ import annotations

from app.services.agent_runtime.egress_pipeline import project_turn


def test_json_and_sse_events_share_public_turn_projection():
    public = project_turn({
        "reply": "你好，我可以帮你梳理职业方向。",
        "session_id": "s1",
        "engine": "supervisor",
        "interaction_mode": "ask",
        "active_subagent": "ask",
        "orchestration": {
            "strategy": "ask",
            "active_subagent": "ask",
            "interaction_mode": "ask",
            "blackboard_refs": ["student.profile_ref"],
            "supervisor_view": {"path": "ask_greeting"},
        },
    }).to_dict()

    json_projection = public
    sse_events = public["events"]
    assert json_projection["reply"]
    assert sse_events[0]["type"] == "turn_start"
    assert sse_events[-1]["type"] == "turn_done"
    assert any(e["type"] == "reply_delta" for e in sse_events)
    assert "blackboard_refs" not in str(json_projection)
    assert "blackboard_refs" not in str(sse_events)
