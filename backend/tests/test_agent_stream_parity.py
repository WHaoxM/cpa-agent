from __future__ import annotations

import json

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


def test_demo_mode_json_and_sse_share_result_without_session_mutation(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")

    def unexpected_session_access():
        raise AssertionError("demo transport must not create or mutate a persisted session")

    monkeypatch.setattr(agent_api, "get_session_manager", unexpected_session_access)
    monkeypatch.setattr(
        agent_api,
        "_run_recorded_supervisor_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("demo SSE must not enter the Supervisor workflow")
        ),
    )

    client = create_app().test_client()
    payload = {
        "message": "hello",
        "mode": "demo",
        "session_id": "demo_transport_parity",
        "interaction_mode": "ask",
    }
    sync = client.post("/api/agent/chat", json=payload)
    stream = client.post("/api/agent/chat/stream", json=payload)

    assert sync.status_code == 200
    assert stream.status_code == 200
    sync_turn = sync.get_json()["data"]
    chunks = stream.get_data(as_text=True).splitlines()
    stream_events = [
        json.loads(line.removeprefix("data: "))
        for line in chunks
        if line.startswith("data: {")
    ]

    assert chunks[0] == ": connected"
    assert sync_turn["engine"] == "demo"
    assert sync_turn["session_id"] == payload["session_id"]
    # These are two distinct HTTP turns, so each projection owns a fresh turn_id.
    # Transport-visible event semantics must otherwise stay identical.
    def without_turn_id(events):
        return [{key: value for key, value in event.items() if key != "turn_id"} for event in events]

    assert without_turn_id(stream_events) == without_turn_id(sync_turn["events"])
    assert "data: [DONE]" in chunks
