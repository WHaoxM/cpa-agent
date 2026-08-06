from __future__ import annotations

from app.services.agent_runtime.egress_pipeline import (
    project_session_context,
    project_session_messages,
    project_turn,
)


def test_public_turn_drops_internal_orchestration_and_redacts_pii():
    public = project_turn({
        "reply": "已完成。phone=13800138000 email=a@b.com portrait.current 已更新",
        "tool_calls": [{
            "tool_name": "db_query",
            "parameters": {"student_id": "student_001", "profile_json": {"x": 1}},
            "result_preview": "查询结果 (1 条): phone=13800138000 profile_json={...}",
        }],
        "thinking_steps": [
            "portrait.current 为空",
            "我会先基于用户已提供的信息说明边界。",
        ],
        "session_id": "s1",
        "elapsed_seconds": 1.2,
        "active_subagent": "qa_subagent",
        "engine": "supervisor",
        "interaction_mode": "agent",
        "permission_denials": [{
            "tool_name": "db_query",
            "raw_args": {"token": "secret"},
            "message": "Ask 模式未允许这项操作",
        }],
        "orchestration": {
            "strategy": "agent",
            "active_subagent": "qa_subagent",
            "interaction_mode": "agent",
            "blackboard_refs": ["portrait.current"],
            "supervisor_view": {
                "path": "agent_attachments",
                "file_ids": ["file_secret"],
                "doc_pack": {"all_complete": True},
            },
            "plan_results": [{"subagent": "qa", "ok": True}],
            "permission_denials": [],
        },
        "blackboard": {"portrait.current": {"profile_json": {"secret": True}}},
    }).to_dict()

    blob = str(public)
    assert "blackboard_refs" not in blob
    assert "supervisor_view" not in blob
    assert "plan_results" not in blob
    assert "file_secret" not in blob
    assert "profile_json" not in blob
    assert "13800138000" not in blob
    assert "a@b.com" not in blob
    assert public["orchestration"]["progress"]["document_complete"] is True
    assert public["permission_denials"][0]["code"] == "permission_denied"
    assert public["tool_calls"][0]["egress"] == "redacted"


def test_session_public_views_do_not_expose_raw_context_metadata():
    ctx = project_session_context({
        "interaction_mode": "agent",
        "blackboard": {"match.current": {"posting_json": {"x": 1}}},
        "orchestration": {
            "strategy": "agent",
            "blackboard_refs": ["match.current"],
            "supervisor_view": {"file_ids": ["file_secret"], "path": "x"},
        },
    })
    assert "blackboard" not in ctx
    assert "blackboard_refs" not in str(ctx)
    assert "file_secret" not in str(ctx)

    messages = project_session_messages([
        {
            "role": "assistant",
            "content": "email=a@b.com db_query",
            "metadata": {
                "attachment_ids": ["file_secret"],
                "orchestration": {
                    "strategy": "ask",
                    "blackboard_refs": ["attachments.mounted"],
                },
            },
        }
    ])
    blob = str(messages)
    assert "file_secret" not in blob
    assert "blackboard_refs" not in blob
    assert "a@b.com" not in blob
