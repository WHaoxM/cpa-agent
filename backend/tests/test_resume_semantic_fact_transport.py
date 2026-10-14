"""Map/reduce transports quoted facts; extraction is not a second scoring model."""
import hashlib
import json

from app.pipeline.entity_extractor.resume_structure import (
    _canonical_response_format, _merge_grounded_facts, _semantic_fact_instruction, extract_canonical_profile,
)
from config.loader import get_rules_config


def test_prompt_exposes_configured_categories_without_name_heuristics():
    instruction = _semantic_fact_instruction()
    for value in get_rules_config().scoring_tables["major_category_score"]:
        if value:
            assert value in instruction
    assert "可复制该专业全称" in instruction
    assert "不得将普通本科等同一本或二本" in instruction
    assert "行为经历不能直接充当能力等级" in instruction


def test_chunk_evidence_survives_merge_omission_and_binds_full_source():
    text = "材料" * 8000 + "\n计算机科学与技术，沟通能力强"

    class Client:
        def chat(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            facts = {}
            if "文本：" in prompt:
                assert "可复制该专业全称" in prompt
                if "计算机科学与技术，沟通能力强" in prompt:
                    facts = {
                        "major_category": {"value": "计算机科学与技术", "evidence": "计算机科学与技术，沟通能力强"},
                        "communication": {"value": "强", "evidence": "沟通能力强"},
                        "teamwork": {"value": "强", "evidence": "捏造的团队合作引文"},
                    }
            return json.dumps({"school": "大学", "major": "计算机科学与技术",
                               "semantic_facts": facts}, ensure_ascii=False)

    result = extract_canonical_profile(Client(), text)
    assert result["structure_path"] == "chunk_merge"
    assert set(result["semantic_facts"]) == {"major_category", "communication"}
    digest = hashlib.sha256(text.encode()).hexdigest()
    assert result["source_sha256"] == digest
    assert all(fact["source_sha256"] == digest for fact in result["semantic_facts"].values())
    assert result["semantic_fact_issues"][0]["reason"] == "evidence_not_in_source"
    assert result["semantic_fact_next_action"] == "retry_fact_extraction"


def test_conflicting_map_values_are_not_resolved_by_arbitrary_precedence():
    result = _merge_grounded_facts({}, [
        {"semantic_facts": {"communication": {"value": "强", "evidence": "沟通强"}}},
        {"semantic_facts": {"communication": {"value": "弱", "evidence": "沟通弱"}}},
    ], "旧自述沟通弱，新自述沟通强")
    assert "communication" not in result.get("semantic_facts", {})
    assert result["semantic_fact_conflicts"] == ["communication"]


def test_chunk_merge_keeps_retry_priority_when_unmapped_and_bad_quote_coexist():
    result = _merge_grounded_facts({}, [{
        "semantic_facts": {"school_tier": {"value": "其他理工", "evidence": "其他理工"}},
        "semantic_fact_issues": [{"field": "teamwork", "reason": "evidence_not_in_source"}],
    }], "其他理工")
    assert result["semantic_facts"]["school_tier"]["value"] == "其他理工"
    assert {"field": "school_tier", "reason": "value_not_in_scoring_category"} in result["semantic_fact_issues"]
    assert {"field": "teamwork", "reason": "evidence_not_in_source", "window_offset": 0} in result["semantic_fact_issues"]
    assert result["semantic_fact_next_action"] == "retry_fact_extraction"


def test_merge_cannot_introduce_new_semantic_facts():
    class Client:
        def chat(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            facts = ({"communication": {"value": "强", "evidence": "强"}}
                     if "partials：" in prompt else {})
            return json.dumps({"school": "大学", "semantic_facts": facts})
    result = extract_canonical_profile(Client(), "强" * 15000)
    assert not result.get("semantic_facts")


def test_wire_schema_closes_objects_and_requires_nullable_fact_slots():
    response_format = _canonical_response_format()
    schema = response_format["json_schema"]["schema"]
    assert response_format["json_schema"]["strict"] is True
    assert "semantic_facts" in schema["required"]
    facts = schema["$defs"]["_FactSet"]
    assert facts["additionalProperties"] is False
    assert "major_category" in facts["required"]
    assert {"type": "null"} in facts["properties"]["major_category"]["anyOf"]


def test_malformed_provider_fact_is_explicit_extraction_failure():
    class Client:
        def chat(self, **kwargs):
            assert kwargs["response_format"]["type"] == "json_schema"
            return json.dumps({"school": "大学", "semantic_facts": {"major_category": ["文科"]}})
    result = extract_canonical_profile(Client(), "文科")
    assert "error" in result
    assert not result.get("semantic_facts")
