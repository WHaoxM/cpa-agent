from app.services.agent_runtime.egress_pipeline import (
    project_session_messages,
    project_turn,
)


def test_project_turn_builds_parts_artifacts_and_events():
    turn = project_turn({
        "reply": "已生成任务卡。",
        "session_id": "session_1",
        "tool_calls": [{
            "tool_name": "pipeline_run",
            "summary": "pipeline_run 已完成",
            "parameters": {"task_id": "task_abc123"},
            "artifact_refs": [{
                "artifact_id": "art_1",
                "kind": "pipeline_task",
                "status": "completed",
                "title": "画像生成管线",
                "summary": "画像生成完成",
                "preview": {"task_id": "task_abc123"},
                "actions": [{"id": "open_pipeline_task", "label": "查看任务"}],
            }],
        }],
        "orchestration": {
            "strategy": "agent",
            "active_subagent": "profile",
            "supervisor_view": {"plan_sketch": ["画像", "匹配"]},
        },
    }).to_dict()

    assert turn["turn_id"].startswith("turn_")
    assert any(part["type"] == "progress" for part in turn["parts"])
    assert any(part["type"] == "tool_call" for part in turn["parts"])
    assert any(part["type"] == "artifact_ref" for part in turn["parts"])
    assert turn["artifacts"][0]["artifact_id"] == "art_1"
    assert [event["type"] for event in turn["events"]][0] == "turn_start"
    assert turn["events"][-1]["type"] == "turn_done"


def test_project_session_messages_parts_fallback_and_metadata_parts():
    rows = [
        {"role": "assistant", "content": "旧消息", "timestamp": "2026-07-21T00:00:00"},
        {
            "message_id": "msg_1",
            "role": "assistant",
            "content": "新消息",
            "metadata": {
                "turn_id": "turn_1",
                "interaction_mode": "agent",
                "parts_v1": [{"type": "notice", "level": "info", "text": "已签发"}],
            },
        },
    ]

    public = project_session_messages(rows)

    assert public[0]["parts"] == [{"type": "text", "text": "旧消息"}]
    assert public[1]["id"] == "msg_1"
    assert public[1]["turn_id"] == "turn_1"
    assert public[1]["parts"][0]["type"] == "notice"


def test_missing_slots_progress_summary_lists_items_not_list_characters():
    turn = project_turn({
        "reply": "请补充必要信息。",
        "orchestration": {
            "supervisor_view": {
                "missing_slots": ["目标岗位", "期望城市", "毕业时间", "工作年限", "第五项"],
            },
        },
    }).to_dict()

    progress = next(part for part in turn["parts"] if part["type"] == "progress")
    assert progress["summary"] == "还需要补充：目标岗位、期望城市、毕业时间、工作年限"
