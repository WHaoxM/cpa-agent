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


def test_evidence_summary_stays_structured_through_events_and_history_without_identifiers():
    raw_part = {
        "type": "evidence_summary",
        "title": "secret title must be normalized",
        "status": "partial",
        "sources": [
            {"label": "岗位报告", "status": "read", "uri": "career://me/reports/report_secret",
             "version": "sha256-secret"},
            {"label": "已确认简历", "status": "unavailable", "student_id": "acct_secret"},
        ],
        "scope_note": "resume_not_cross_checked",
        "internal_uri": "career://me/resume",
    }
    turn = project_turn({"reply": "报告建议先核对技能要求。", "parts": [
        {"type": "text", "text": "报告建议先核对技能要求。"}, raw_part,
    ], "session_id": "session_1"}).to_dict()

    summary = next(part for part in turn["parts"] if part["type"] == "evidence_summary")
    assert summary == {
        "type": "evidence_summary", "title": "本轮参考资料", "status": "partial",
        "sources": [
            {"label": "岗位报告", "status": "read"},
            {"label": "已确认简历", "status": "unavailable"},
        ],
        "scope_note": "resume_not_cross_checked",
    }
    assert any(event["type"] == "evidence_summary" for event in turn["events"])
    assert "career://" not in str(turn["parts"])
    assert "sha256" not in str(turn["parts"])
    assert "acct_secret" not in str(turn["parts"])

    history = project_session_messages([{
        "role": "assistant", "content": turn["reply"],
        "metadata": {"parts_v1": turn["parts"]},
    }])
    assert history[0]["parts"] == turn["parts"]


def test_data_card_snapshot_is_typed_stable_and_replayed_from_message_metadata(monkeypatch):
    import json

    from app.services.session_manager import SessionManager

    source_part = {
        "type": "data_card",
        "schema_version": 1,
        "generated_at": "2026-10-23T10:00:00Z",
        "title": "岗位对照",
        "card_type": "comparison",
        "source_refs": [{
            "source_id": "repo:reviewed-jobs:v1",
            "label": "已发布岗位目录",
            "version": "v1",
            "url": "https://example.org/jobs/123",
        }],
        "items": [
            {"label": "第二项", "value": ["Python", "SQL"]},
            {"label": "第三项", "value": ["Java", "SQL"]},
        ],
        "private_prompt": "must be rejected",
    }
    # Unknown component fields reject the whole snapshot instead of being
    # forwarded alongside an otherwise valid card.
    assert not any(p["type"] == "data_card" for p in project_turn({"parts": [source_part]}).parts)

    source_part.pop("private_prompt")
    turn = project_turn({"reply": "已完成对照。", "parts": [source_part]}).to_dict()
    card = next(part for part in turn["parts"] if part["type"] == "data_card")
    assert card["schema_version"] == 1
    assert card["component_id"].startswith("cmp_")
    assert card["generated_at"] == source_part["generated_at"]
    assert all(item["entity_id"].startswith("ent_") for item in card["items"])
    assert card["source_refs"][0]["source_id"] == "repo:reviewed-jobs:v1"
    assert any(event["type"] == "component" for event in turn["events"])

    # Simulate the exact JSON payload stored in conversation_message.thinking_json,
    # then decode it through the existing row projector and history egress.
    stored = {}

    class MysqlStub:
        def execute(self, _sql, params, *, commit=False):
            stored.update(params)

    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: MysqlStub())
    manager = SessionManager.__new__(SessionManager)
    manager._save_message_to_mysql(
        "session_1", "assistant", turn["reply"], {"parts_v1": turn["parts"]},
        message_id="msg_1",
    )
    decoded = manager._mysql_row_to_message({
        "message_id": "msg_1", "role": "assistant", "content": turn["reply"],
        "thinking_json": stored["thinking_json"],
    })
    history = project_session_messages([decoded])
    assert history[0]["id"] == "msg_1"
    assert history[0]["parts"] == turn["parts"]
    assert history[0]["parts"][0]["component_id"] == card["component_id"]

    manager.get_messages = lambda *_args, **_kwargs: [decoded]
    manager._get_messages_from_mysql = lambda *_args, **_kwargs: []
    agent_history = manager.get_history_for_agent("session_1")
    assert agent_history[0]["message_id"] == "msg_1"
    assert agent_history[0]["parts"] == turn["parts"]


def test_data_card_replay_rejects_unsafe_or_unregistered_component_fields():
    from app.services.agent_runtime.turn_contract import normalize_message_parts

    base = {
        "type": "data_card", "schema_version": 1, "title": "公开岗位",
        "card_type": "list", "source_refs": [{"source_id": "repo:jobs:v1", "label": "目录"}],
        "items": [{"label": "后端开发", "value": "岗位 A"}],
    }
    unsafe = [
        {**base, "on_click": "fetch('/private')"},
        {**base, "source_refs": [{"label": "伪造", "url": "javascript:alert(1)"}]},
        {**base, "items": [{"label": "岗位", "value": {"html": "<script>"}}]},
    ]
    for card in unsafe:
        assert normalize_message_parts([card]) == []

    valid = normalize_message_parts([base])
    assert valid[0]["component_id"].startswith("cmp_")
    assert normalize_message_parts(valid) == valid


def test_action_plan_evidence_summary_projection_keeps_unverified_label():
    turn = project_turn({"reply": "可以根据这段练习记录继续复盘。", "parts": [
        {"type": "evidence_summary", "title": "本轮参考资料", "status": "partial",
         "sources": [{"label": "一周行动计划", "status": "read",
                      "uri": "career://me/action-plans/plan_secret"},
                     {"label": "练习附件", "status": "unavailable",
                      "uri": "file://internal-secret"}],
         "scope_note": "student_submitted_unverified"},
    ]}).to_dict()
    summary = next(part for part in turn["parts"] if part["type"] == "evidence_summary")
    assert summary == {
        "type": "evidence_summary", "title": "本轮参考资料", "status": "partial",
        "sources": [
            {"label": "一周行动计划", "status": "read"},
            {"label": "练习附件", "status": "unavailable"},
        ],
        "scope_note": "student_submitted_unverified",
    }
    assert "career://" not in str(turn["parts"])


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


def test_execution_progress_exposes_only_real_step_outcomes():
    turn = project_turn({
        "reply": "已整理出建议。",
        "orchestration": {
            "strategy": "agent",
            "supervisor_view": {
                "executed_steps": [
                    {"role": "qa", "status": "completed", "skill_ids": ["school-context"]},
                    {"role": "admin", "status": "completed"},
                    {"role": "profile", "status": "not_completed"},
                ],
            },
        },
    }).to_dict()
    progress = next(part for part in turn["parts"] if part["type"] == "progress")
    assert progress["progress"]["executed_steps"] == [
        {"label": "资料问答", "status": "completed"},
        {"label": "职业画像", "status": "not_completed"},
    ]
    assert "admin" not in str(progress)
    assert "school-context" not in str(progress)


def test_workbench_turn_does_not_receive_synthesized_thinking():
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    result = ModeRuntime()._pack(
        reply="回答",
        session_id="s1",
        start=0,
        active_subagent="qa_subagent",
        strategy="agent",
        blackboard=Blackboard(),
        message="帮我分析",
        thinking_steps=["模型独白应不出站"],
    )
    assert result["thinking_steps"] == []
    assert result["orchestration"]["thinking_steps"] == []


def test_exploration_evidence_label_survives_public_message_projection():
    from app.services.agent_runtime.turn_contract import _normalize_evidence_summary
    result = _normalize_evidence_summary({'sources': [{'label': '本轮方向探索', 'status': 'read'}], 'status': 'complete'})
    assert result['sources'] == [{'label': '本轮方向探索', 'status': 'read'}]
