"""Small contracts for the report-gap handoff, independent of model quality."""

import json

import pytest

from app.services.agent_runtime import action_plan_handoff as handoff
from app.services.agent_runtime.egress_pipeline import project_session_messages, project_turn


def test_offer_requires_read_report_resume_current_versions_and_specific_gap():
    pack = {
        "status": "partial", "coverage": {"complete": True}, "unavailable": [],
        "sources": [
            {"uri": "career://me/reports/report_12345678", "version": "r1", "status": "partial"},
            {"uri": "career://me/resume", "version": "resume_task_v1", "status": "completed"},
        ],
    }
    offer = handoff.build_action_plan_offer(
        "岗位报告指出一个 Python 项目练习的差距，简历暂未说明是否做过。", pack,
    )
    assert offer and offer["phase"] == "offered"
    assert offer["report_version"] == "r1"
    assert offer["resume_version"] == "resume_task_v1"
    assert offer["handoff_id"].startswith("aph_")

    pack["sources"] = pack["sources"][:1]
    assert handoff.build_action_plan_offer("报告指出具体差距。", pack) is None
    pack["sources"].append({"uri": "career://me/resume", "version": "resume_task_v1", "status": "partial"})
    assert handoff.build_action_plan_offer("报告指出具体差距。", pack) is None
    assert handoff.build_action_plan_offer("看起来不错。", pack) is None


def test_source_validation_rechecks_owner_report_and_current_resume(monkeypatch):
    observed = []

    def read(_mysql, *, student_id, uri, section):
        observed.append((student_id, uri, section))
        if uri.startswith("career://me/reports/"):
            return {
                "success": True, "business_artifact_verified": True,
                "status": "partial", "version": "report_v1",
                "data": {"sources": {"source_confirmation_task_id": "resume_v1"}},
            }
        return {"success": True, "status": "completed", "version": "resume_v1"}

    monkeypatch.setattr("app.services.agent_runtime.resource_catalog.read_platform_resource", read)
    context = {
        "report_uri": "career://me/reports/report_12345678",
        "report_version": "report_v1", "resume_version": "resume_v1",
    }
    assert handoff.verify_action_plan_sources(object(), student_id="owner_1", context=context) == (True, "current")
    assert {row[0] for row in observed} == {"owner_1"}
    assert {row[2] for row in observed} == {"overview"}

    context["resume_version"] = "resume_old"
    assert handoff.verify_action_plan_sources(object(), student_id="owner_1", context=context) == (False, "expired")


def test_plan_model_output_requires_three_to_five_clean_steps(monkeypatch):
    class FakeLLM:
        def chat(self, *, messages, **_kwargs):
            request = json.loads(messages[-1]["content"])
            assert request["weekly_time_commitment"] == "每周 5 小时"
            return json.dumps({"steps": [
                {"title": "拆出一项技能", "detail": "从报告要求中选一个可练习的具体技能。"},
                {"title": "做一份小练习", "detail": "完成一次 Python 数据整理并保存结果。"},
                {"title": "复盘并留证", "detail": "记录结果和仍需补充的证据。 career://me/reports/report_deadbeef"},
            ]}, ensure_ascii=False)

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    steps = handoff.generate_weekly_steps(
        gap_summary="报告指出数据练习不足。", weekly_hours="每周 5 小时",
        evidence_pack={"items": [{"data": {"requirements": ["Python"]}}]},
    )
    assert len(steps) == 3
    assert "career://" not in steps[2]["detail"]


def test_plan_model_rejects_non_structured_or_wrong_count(monkeypatch):
    class FakeLLM:
        def chat(self, **_kwargs):
            return '{"steps":[{"title":"只有一步","detail":"不能作为一周计划"}]}'

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    with pytest.raises(ValueError, match="3 至 5 个"):
        handoff.generate_weekly_steps(
            gap_summary="某项差距", weekly_hours="2小时",
            evidence_pack={"items": []},
        )


def test_handoff_part_round_trips_http_sse_and_history_without_source_ids():
    part = {
        "type": "action_plan_handoff", "handoff_id": "aph_" + "c" * 32, "stage": "offer",
        "report_uri": "career://me/reports/should_not_escape",
    }
    turn = project_turn({
        "reply": "报告指出一个具体差距。", "parts": [{"type": "text", "text": "报告指出一个具体差距。"}, part],
        "session_id": "sess_1", "interaction_mode": "ask",
    }).to_dict()
    handoff_parts = [item for item in turn["parts"] if item.get("type") == "action_plan_handoff"]
    assert handoff_parts == [{
        "type": "action_plan_handoff", "handoff_id": "aph_" + "c" * 32, "stage": "offer",
    }]
    assert any(event.get("type") == "action_plan_handoff" for event in turn["events"])
    history = project_session_messages([{
        "role": "assistant", "content": "报告指出一个具体差距。",
        "metadata": {"parts_v1": turn["parts"]},
    }])
    assert any(item.get("type") == "action_plan_handoff" for item in history[0]["parts"])
    assert "should_not_escape" not in str(history)
