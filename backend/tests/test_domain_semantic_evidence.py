import json

from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
from app.pipeline.entity_extractor.resume_structure import (
    _ground_semantic_facts, extract_canonical_profile, confirmed_semantic_fact, validate_structure_truth,
)
from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
from app.pipeline.entity_extractor.matcher import SkillMatcher, calculate_match_score


def test_titles_and_short_tail_do_not_drop_text():
    text = "没有传统标题\n经历并非工作经历\n" + "尾部材料" * 5000 + "\nGo"
    blocks = NSLEPipeline()._phase1_parse(text, "resume")
    assert "".join(b.text for b in blocks) == text
    assert blocks[0].source["end"] == len(text)


def test_missing_provider_never_dictionary_fallback():
    result = NSLEPipeline(use_deepseek=False).run_resume_text("Python 沟通 领导力")
    assert result["success"] is False
    assert "semantic_extractor_required" in result["error"]


def test_keyword_stuffing_is_not_soft_skill_evidence():
    profile = {"skills": ["Python"], "projects": [{"description": "沟通 领导 抗压 负责 主导"}] * 20}
    d3 = SkillMatcher()._calc_d3_detailed(profile)
    assert d3["score"] is None
    assert d3["status"] == "partial"
    portrait = PortraitGenerator().generate_student_portrait("owner", [], student_profile=profile)
    assert portrait["summary"]["competitiveness_score"] is None
    assert portrait["D3_quality"]["communication"]["score"] is None


def test_confirmation_and_source_binding_required():
    profile = _ground_semantic_facts({}, {"semantic_facts": {
        "communication": {"value": "强", "evidence": "沟通能力强"},
        "teamwork": {"value": "强", "evidence": "不存在的引文"},
    }}, "本人沟通能力强")
    assert confirmed_semantic_fact(profile, "communication") is None
    profile["_resume_confirmation"] = {"task_id": "persisted-confirmation"}
    assert confirmed_semantic_fact(profile, "communication") == "强"
    assert confirmed_semantic_fact(profile, "teamwork") is None
    profile["source_sha256"] = "changed"
    assert confirmed_semantic_fact(profile, "communication") is None


def test_unmapped_school_tier_is_retained_and_flagged_without_blocking_l0():
    """A quote can be real while its category is unusable for scoring."""
    profile = _ground_semantic_facts({"school": "某大学", "major": "其他理工", "projects": [{"name": "项目"}]}, {"semantic_facts": {
        "school_tier": {"value": "其他理工", "evidence": "其他理工"},
        "major_category": {"value": "其他理工", "evidence": "其他理工"},
    }}, "其他理工")
    profile["_resume_confirmation"] = {"task_id": "confirmed"}

    assert confirmed_semantic_fact(profile, "school_tier") == "其他理工"
    assert confirmed_semantic_fact(profile, "major_category") == "其他理工"
    assert {"field": "school_tier", "reason": "value_not_in_scoring_category"} in profile["semantic_fact_issues"]
    assert profile["semantic_fact_next_action"] == "review_unmapped_scoring_category"
    assert validate_structure_truth(profile)["ok"] is True


def test_legacy_confirmed_wrong_school_category_still_stops_scoring():
    """Old persisted snapshots must not become D1=0 or a completed portrait."""
    text = "学校层级其他理工；专业计算机科学与技术；沟通强；团队强；抗压强；负责强"
    profile = _ground_semantic_facts({"degree": "本科", "skills": ["Python"]}, {"semantic_facts": {
        "major_category": {"value": "计算机科学与技术", "evidence": "计算机科学与技术"},
        "communication": {"value": "强", "evidence": "沟通强"},
        "teamwork": {"value": "强", "evidence": "团队强"},
        "stress_tolerance": {"value": "强", "evidence": "抗压强"},
        "responsibility": {"value": "强", "evidence": "负责强"},
    }}, text)
    profile["semantic_facts"]["school_tier"] = {
        "value": "其他理工", "evidence": "其他理工", "source_sha256": profile["source_sha256"],
    }
    profile["_resume_confirmation"] = {"task_id": "confirmed"}

    portrait = PortraitGenerator().generate_student_portrait("owner", [{"name": "Python"}], student_profile=profile)
    assert portrait["D1_basic"]["school_tier"] == "其他理工"
    assert portrait["D1_basic"]["sub_school"] is None
    assert portrait["D1_basic"]["sub_major"] == 25
    assert portrait["D3_quality"]["dimension_score"] is not None
    assert portrait["status"] == "partial"
    assert portrait["missing_fields"] == ["school_tier"]
    assert portrait["summary"]["competitiveness_score"] is None


def test_explicit_confirmed_facts_use_existing_score_tables():
    facts = {key: {"value": "强", "evidence": "强"} for key in (
        "communication", "teamwork", "stress_tolerance", "responsibility")}
    profile = _ground_semantic_facts({}, {"semantic_facts": facts}, "强")
    profile["_resume_confirmation"] = {"task_id": "confirmed"}
    assert SkillMatcher()._calc_d3_detailed(profile)["score"] == 90
    assert PortraitGenerator()._calc_d3_quality([], profile)["dimension_score"] == 80


def test_match_partial_does_not_pass_or_invent_score(monkeypatch):
    monkeypatch.setattr(SkillMatcher, "_normalize_entity", lambda self, name, *a, **k: name)
    monkeypatch.setattr(SkillMatcher, "_apply_external_rule", lambda self, value, *a: value)
    result = calculate_match_score([{"name": "Python"}], [{"name": "Python"}],
                                   {"degree": "本科", "skills": ["Python"]}, {})
    assert result["total_score"] is None
    assert result["match_result"] is False
    assert result["status"] == "partial"
    assert result["match_level"] == "unknown"


def test_partial_chunk_failure_is_explicit():
    class Client:
        calls = 0
        def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("provider unavailable")
            return json.dumps({"school": "大学", "major": "专业", "projects": [{"name": "项目"}]})
    result = extract_canonical_profile(Client(), "完整材料" * 4000)
    assert result["error"] == "partial_chunk_extraction"
    assert result["failed_offsets"]
    assert result["next_action"] == "retry_extraction"
