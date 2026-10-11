import json
from types import SimpleNamespace

import pytest

from app.pipeline.entity_extractor.llm_skill_extractor import (
    LLMSkillExtractor, SkillExtractionError,
)


def candidate(**updates):
    value = dict(name="通信协议设计", level="未说明", category="通信工程",
                 skill_type="hard", confidence=0.7, evidence="完成通信协议设计")
    value.update(updates)
    return value


def extractor_for(payload):
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if isinstance(payload, Exception):
            raise payload
        return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    extractor = LLMSkillExtractor()
    extractor._llm_client = SimpleNamespace(chat=chat)
    return extractor, calls


def test_full_tail_and_model_classification_preserved_without_alias_rewrite():
    text = "背景资料。" * 1500 + "完成通信协议设计"
    extractor, calls = extractor_for({"skills": [candidate()], "relations": []})
    result = extractor.extract_document(text)
    assert text in calls[0]["messages"][0]["content"]
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert result["input_chars"] == len(text)
    assert result["skills"][0]["skill_type"] == "hard"
    assert result["skills"][0]["name"] == "通信协议设计"
    assert result["skills"][0]["source"] == "llm_candidate"
    assert result["relations"] == []


def test_legitimate_empty_does_not_fall_back_to_dictionary():
    extractor, _ = extractor_for({"skills": [], "relations": []})
    assert extractor.extract("从未使用 Python，岗位要求 Python。") == []


@pytest.mark.parametrize("payload", [
    RuntimeError("context_budget_exceeded"),
    '{"skills": [',
    {"skills": [candidate(skill_type="invented")], "relations": []},
    {"skills": [candidate(evidence="not in source")], "relations": []},
    {"skills": [candidate(confidence=2)], "relations": []},
    {"skills": [candidate()], "relations": [
        {"source": "通信协议设计", "target": "幻觉", "type": "requires", "evidence": "完成通信协议设计"}
    ]},
])
def test_invalid_or_unavailable_model_does_not_produce_success(payload):
    extractor, _ = extractor_for(payload)
    with pytest.raises(SkillExtractionError):
        extractor.extract("完成通信协议设计")


def test_disabled_llm_is_explicit_not_keyword_mode():
    extractor, calls = extractor_for({"skills": [], "relations": []})
    with pytest.raises(SkillExtractionError, match="semantic_extraction_required"):
        extractor.extract("Python", use_llm=False)
    assert not calls


def test_relation_uses_model_evidence_not_line_cooccurrence():
    payload = {"skills": [candidate(), candidate(name="Python", evidence="Python")],
               "relations": []}
    extractor, _ = extractor_for(payload)
    assert extractor.extract_document("完成通信协议设计 Python")["relations"] == []


def test_pipeline_failure_never_writes_guessed_skills(monkeypatch):
    from app.pipeline.entity_extractor import entity_pipeline
    extractor, _ = extractor_for(RuntimeError("model offline"))
    monkeypatch.setattr(entity_pipeline, "extract_skills_with_relations",
                        lambda text, use_llm: extractor.extract_document(text, use_llm))
    writes = []
    pipeline = entity_pipeline.EntityPipeline.__new__(entity_pipeline.EntityPipeline)
    pipeline.use_llm = True
    pipeline.graph_storage = SimpleNamespace(save_skills=lambda **kw: writes.append(kw))
    result = pipeline.run_l1_extract("Python", entity_id="test-owned")
    assert result["success"] is False
    assert not writes
