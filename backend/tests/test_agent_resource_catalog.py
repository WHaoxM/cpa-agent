"""One main Agent can discover/read current records from multiple features."""

from types import SimpleNamespace

import pytest

from app.services.agent_runtime import resource_catalog
from app.services.agent_runtime.media import prepare_turn
from app.services.tools.career_data_read import CareerDataReadTool


def test_catalog_composes_domain_adapters_without_losing_failure_visibility(monkeypatch):
    from app.services.agent_runtime import business_resources, match_report_resources, personal_resources, resume_report_resources

    monkeypatch.setattr(business_resources, "list_current_resources", lambda mysql, student_id: {
        "resources": [{"uri": "career://me/resume", "title": "简历", "status": "completed"}],
    })
    # resource_catalog imported this function at module load; patch its binding.
    monkeypatch.setattr(resource_catalog, "list_current_resources", business_resources.list_current_resources)
    monkeypatch.setattr(match_report_resources, "list_match_report_resources", lambda mysql, student_id: {
        "resources": [{"uri": "career://me/reports", "title": "报告", "status": "empty"}],
    })
    monkeypatch.setattr(resume_report_resources, "list_resume_report_resources", lambda mysql, student_id: {
        "resources": [{"uri": "career://me/resume-reports", "title": "简历诊断", "status": "empty"}],
    })
    monkeypatch.setattr(personal_resources, "list_personal_resources", lambda mysql, student_id: [
        {"uri": "career://me/action-plans", "title": "计划", "status": "draft"},
    ])
    catalog = resource_catalog.list_platform_resources(object(), student_id="student_001")
    assert [item["uri"] for item in catalog["resources"]] == [
        "career://me/resume", "career://me/reports", "career://me/resume-reports", "career://me/action-plans",
    ]
    assert catalog["status"] == "completed"

    def down(mysql, student_id):
        raise RuntimeError("DB down")

    monkeypatch.setattr(personal_resources, "list_personal_resources", down)
    partial = resource_catalog.list_platform_resources(object(), student_id="student_001")
    assert partial["status"] == "partial"
    assert partial["unavailable_sources"] == [{"source": "personal", "status": "unavailable"}]


def test_resource_ref_is_owner_read_before_stm_and_discards_browser_claims(monkeypatch):
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: object())
    monkeypatch.setattr("app.services.agent_runtime.media.context_mount.build_attachment_mount_card",
                        lambda *args, **kwargs: "")
    calls = []

    def read(mysql, *, student_id, uri, **kwargs):
        calls.append((student_id, uri))
        if student_id != "student_001":
            raise LookupError("other owner")
        return {"success": True, "uri": uri, "status": "draft", "version": "v1",
                "coverage": "overview_only", "data": {"title": "权威标题"}}

    monkeypatch.setattr(resource_catalog, "read_platform_resource", read)
    prepared = prepare_turn.prepare_user_turn(
        "分析这份计划", student_id="student_001",
        content_parts=[{"type": "resource_ref", "uri": "career://me/action-plans/plan_123",
                        "title": "浏览器伪造标题", "status": "completed", "summary": "浏览器伪造正文"}],
    )
    assert calls == [("student_001", "career://me/action-plans/plan_123")]
    assert prepared["parts"][-1] == {"type": "resource_ref", "uri": "career://me/action-plans/plan_123"}
    assert prepared["verified_resource_refs"][0]["status"] == "draft"
    assert "浏览器伪造" not in str(prepared)
    assert "权威标题" not in prepared["attach_card"]  # metadata, not full content
    with pytest.raises(PermissionError):
        prepare_turn.prepare_user_turn("", content_parts=[{
            "type": "resource_ref", "uri": "career://me/action-plans/plan_123",
        }])
    with pytest.raises(LookupError):
        prepare_turn.prepare_user_turn("", student_id="student_other", content_parts=[{
            "type": "resource_ref", "uri": "career://me/action-plans/plan_123",
        }])
    with pytest.raises(ValueError, match="最多引用 4"):
        prepare_turn.prepare_user_turn("", student_id="student_001", content_parts=[
            {"type": "resource_ref", "uri": f"career://me/action-plans/plan_{i}"}
            for i in range(5)
        ])

    monkeypatch.setattr(resource_catalog, "read_platform_resource", lambda *args, **kwargs: {
        "success": True, "status": "not_available", "uri": kwargs["uri"],
    })
    with pytest.raises(LookupError):
        prepare_turn.prepare_user_turn("", student_id="student_001", content_parts=[{
            "type": "resource_ref", "uri": "career://me/reports/report_stale",
        }])


def test_tool_reads_registry_under_request_scope(monkeypatch):
    from flask import Flask
    from app.services.tools import career_data_read

    monkeypatch.setattr(career_data_read, "get_mysql_client", lambda: object())
    seen = []

    def read(mysql, *, student_id, uri, **kwargs):
        seen.append((student_id, uri))
        return {"success": True, "uri": uri, "status": "draft", "data": {"count": 1}}

    monkeypatch.setattr(career_data_read, "read_platform_resource", read)
    app = Flask(__name__)
    with app.test_request_context("/"):
        monkeypatch.setattr("app.api._helpers.resolve_request_scope",
                            lambda: SimpleNamespace(student_id="student_001"))
        result = CareerDataReadTool().execute(operation="read", uri="career://me/action-plans",
                                              student_id="student_other")
    assert '"status": "draft"' in result
    assert seen == [("student_001", "career://me/action-plans")]
