import pytest

from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
from app.services.student_self_report import portrait_current, portrait_scoring_current, validated_items


def test_self_report_cannot_be_attached_to_another_resume_or_serve_as_quote():
    items = {key: None for key in ("communication", "teamwork", "stress_tolerance", "responsibility")}
    items["communication"] = {"level": "强", "example": "我负责组织项目例会，协调各组按期交付共同成果。"}
    profile = {"_resume_confirmation": {"task_id": "task_new"},
               "_user_self_report": {"source_kind": "user_self_report", "revision": 1,
                                     "source_confirmation_task_id": "task_old", "items": items}}
    dimension = PortraitGenerator()._calc_d3_quality([], profile)
    assert dimension["communication"]["score"] is None
    assert dimension["communication"]["provenance"] is None
    assert not portrait_current(profile, {"source_confirmation_task_id": "task_new",
                                          "source_self_report_revision": 0})


def test_self_report_requires_concrete_example_not_claimed_score():
    items = {key: None for key in ("communication", "teamwork", "stress_tolerance", "responsibility")}
    items["communication"] = {"level": "强", "example": "我很会沟通", "score": 100}
    with pytest.raises(ValueError):
        validated_items(items)


def test_legacy_zero_growth_without_evidence_is_not_current():
    profile = {"_resume_confirmation": {"task_id": "task_current"}}
    legacy = {"source_confirmation_task_id": "task_current",
              "D4_potential": {"dimension_score": 0, "evidence": []}}
    assert not portrait_scoring_current(legacy)
    assert not portrait_current(profile, legacy)
    with_evidence = {**legacy, "D4_potential": {
        "dimension_score": 0, "evidence": ["成绩: 已核对"]}}
    assert portrait_current(profile, with_evidence)
    unknown = {**legacy, "D4_potential": {
        "dimension_score": None, "evidence": [], "missing_fields": ["growth_evidence"]}}
    assert portrait_current(profile, unknown)
