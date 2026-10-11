import json

from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
from app.pipeline.entity_extractor.resume_structure import (
    _ground_semantic_facts, extract_canonical_profile, confirmed_semantic_fact,
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
