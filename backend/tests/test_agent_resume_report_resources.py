"""Current confirmed resume report can be read by the main Agent, not browser claims."""

from copy import deepcopy

import pytest

from app.services.agent_runtime import resource_catalog, resume_report_resources as resources
from app.services import resume_diagnostic_report as service
from app.services import job_catalog_listing


RID = "resume_" + "a" * 32
URI = f"{resources.RESUME_REPORTS_URI}/{RID}"
REPORT = {
    "report_id": RID, "report_type": "resume_diagnostic", "status": "partial",
    "source_confirmation_task_id": "task_current", "coverage": {"page_count": 2},
    "missing_sections": ["unparsed_source_pages"],
    "limitations": ["没有岗位来源，不给出匹配分"],
    "quality_status": "model_interpretation_unverified",
    "analysis": {"summary": {"text": "需核对的模型综述", "evidence_ids": ["projects_0"]},
                 "strengths": [{"text": "项目经验", "evidence_ids": ["projects_0"]}],
                 "questions": []},
    "chapters": [
        {"id": "projects", "title": "项目", "items": [
            {"label": f"项目{i}", "evidence_refs": [{"page": i + 1}]} for i in range(5)
        ]},
        {"id": "skills", "title": "技能", "items": [{"label": "Python", "evidence_refs": [{"page": 1}]}]},
    ],
}


def test_list_and_page_current_resume_report(monkeypatch):
    seen = []

    def read(mysql, *, student_id, report_id=None):
        seen.append((student_id, report_id))
        if student_id != "owner" or report_id not in (None, RID):
            raise LookupError("not owner")
        return deepcopy(REPORT)

    monkeypatch.setattr(service, "read_resume_diagnostic", read)
    catalog = resources.list_resume_report_resources(object(), student_id="owner")
    assert [r["uri"] for r in catalog["resources"]] == [resources.RESUME_REPORTS_URI, URI]
    assert catalog["resources"][1]["business_artifact_verified"] is True
    assert {item["section"]: item["title"] for item in catalog["resources"][1]["section_details"]}[
        "chapter:projects"] == "项目"
    overview = resource_catalog.read_platform_resource(object(), student_id="owner", uri=URI)
    assert overview["data"]["chapter_count"] == 2
    assert "项目0" not in str(overview)
    first = resource_catalog.read_platform_resource(
        object(), student_id="owner", uri=URI, section="chapter:projects", limit=3,
    )
    second = resource_catalog.read_platform_resource(
        object(), student_id="owner", uri=URI, section="chapter:projects", offset=3,
    )
    assert first["coverage"]["has_more"] is True
    assert first["coverage"]["next_offset"] == 3
    assert second["coverage"]["complete"] is True
    assert len(first["data"] + second["data"]) == 5
    analysis = resource_catalog.read_platform_resource(object(), student_id="owner", uri=URI,
                                                       section="analysis", limit=1)
    assert analysis["data"][0]["text"] == "需核对的模型综述"
    assert analysis["quality_status"] == "model_interpretation_unverified"
    assert analysis["coverage"]["has_more"] is True
    assert all(sid == "owner" for sid, _ in seen)


def test_other_owner_stale_or_missing_report_not_exposed(monkeypatch):
    def read(mysql, *, student_id, report_id=None):
        if student_id != "owner":
            raise LookupError("not owner")
        return deepcopy(REPORT)

    monkeypatch.setattr(service, "read_resume_diagnostic", read)
    with pytest.raises(ValueError):
        resource_catalog.read_platform_resource(object(), student_id="owner", uri=URI, section="chapter:unknown")
    monkeypatch.setattr(service, "read_resume_diagnostic", lambda mysql, *, student_id, report_id=None: (
        (_ for _ in ()).throw(LookupError("stale or missing"))
    ))
    catalog = resources.list_resume_report_resources(object(), student_id="owner")
    assert catalog["resources"][0]["status"] == "empty"
    with pytest.raises(LookupError):
        resource_catalog.read_platform_resource(object(), student_id="other", uri=URI)


def test_job_catalog_resource_is_live_and_owner_scoped(monkeypatch):
    def read(mysql, *, student_id, report_id=None):
        if student_id != "owner" or report_id not in (None, RID):
            raise LookupError("not owner")
        return deepcopy(REPORT)

    revision = {"value": 1}

    def list_jobs(mysql, *, limit, offset):
        assert (limit, offset) == (2, 0)
        return {"status": "ready", "total": 1, "items": [{
            "job_portrait_id": "job_1", "publication_version": revision["value"],
            "position_name": "AI 产品实习生", "vacancy_validity": "unknown",
        }], "catalog_version": f"catalog_{revision['value']}",
                "catalog_latest_published_at": "2026-10-10T01:00:00", "has_more": False}

    monkeypatch.setattr(service, "read_resume_diagnostic", read)
    monkeypatch.setattr(job_catalog_listing, "list_published_job_candidates", list_jobs)
    first = resource_catalog.read_platform_resource(
        object(), student_id="owner", uri=URI, section="job_catalog", limit=2,
    )
    revision["value"] = 2
    second = resource_catalog.read_platform_resource(
        object(), student_id="owner", uri=URI, section="job_catalog", limit=2,
    )
    assert first["version"] == "catalog_1"
    assert second["version"] == "catalog_2"
    assert second["data"][0]["publication_version"] == 2
    assert second["source_confirmation_task_id"] == "task_current"
    assert second["coverage"]["complete"] is True
    with pytest.raises(LookupError):
        resource_catalog.read_platform_resource(
            object(), student_id="other", uri=URI, section="job_catalog", limit=2,
        )
