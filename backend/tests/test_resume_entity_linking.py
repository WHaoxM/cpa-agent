import json
from types import SimpleNamespace

import pytest

from app.services.resume_entity_linking import link_resume_mentions


def adapters(decisions):
    calls = []
    def query(sql, params, read_only):
        assert read_only is True
        if sql.startswith("SHOW INDEXES"):
            return []
        assert "labels(s) = ['PublicSkill']" in sql
        assert "visibility = 'public'" in sql
        calls.append(sql)
        return [{"name": "Python", "category": "language", "source_id": "repo:public-skills:v1"}]
    def chat(**kwargs):
        assert kwargs["response_format"]["type"] == "json_schema"
        return json.dumps({"decisions": decisions})
    return SimpleNamespace(chat=chat), SimpleNamespace(execute_query=query), calls


def test_link_preserves_mention_candidate_and_rationale_without_writes():
    model, db, calls = adapters([dict(mention_index=0, decision="equivalent", candidate_index=0,
                                    rationale="原文明示编程语言 Python")])
    mentions = [{"name": "Python语言", "evidence": "使用Python语言开发"}]
    result = link_resume_mentions(model, mentions, "使用Python语言开发", client=db)
    assert result["status"] == "proposed"
    assert result["mentions"] == mentions
    assert result["candidates"][0]["name"] == "Python"
    assert result["decisions"][0]["rationale"]
    assert len(calls) == 1


@pytest.mark.parametrize("decisions", [[],
    [dict(mention_index=0, decision="equivalent", candidate_index=99, rationale="invented")],
    [dict(mention_index=0, decision="unresolved", candidate_index=0, rationale="inconsistent")],
])
def test_invalid_decision_is_explicit_unresolved(decisions):
    model, db, _ = adapters(decisions)
    result = link_resume_mentions(model, [{"name": "Python", "evidence": "Python"}], "Python", client=db)
    assert result["status"] == "unresolved"
    assert result["decisions"] == []


def test_missing_evidence_never_queries_or_calls_model():
    result = link_resume_mentions(None, [{"name": "Python", "evidence": "invented"}], "source", client=None)
    assert result["reason"] == "evidence_not_in_source"


def test_empty_retrieval_is_explicit_not_a_fabricated_match():
    db = SimpleNamespace(execute_query=lambda *a, **kw: [])
    result = link_resume_mentions(None, [{"name": "Python", "evidence": "Python"}], "Python", client=db)
    assert result["reason"] == "no_retrieved_public_candidates"
    assert result["retrieval"]["degraded"] is True


def test_canonical_extraction_calls_linker_and_keeps_original_skills(monkeypatch):
    from app.pipeline.entity_extractor.resume_structure import extract_canonical_profile
    from app.services import resume_entity_linking
    observed = []
    monkeypatch.setattr(resume_entity_linking, "link_resume_mentions", lambda model, mentions, text:
        observed.append((mentions, text)) or {"status": "proposed"})
    model = SimpleNamespace(chat=lambda **kw: json.dumps({"skills": ["Python语言"],
        "skill_mentions": [{"name": "Python语言", "evidence": "使用Python语言开发"}]}))
    profile = extract_canonical_profile(model, "使用Python语言开发")
    assert observed[0][1] == "使用Python语言开发"
    assert profile["skills"] == ["Python语言"]
    assert profile["entity_linking"]["status"] == "proposed"


def test_optional_entity_output_truncation_does_not_invalidate_full_profile():
    from app.pipeline.entity_extractor.resume_structure import extract_canonical_profile
    calls = []
    text = "完整正文" * 200 + "尾页Python"
    def chat(**kwargs):
        calls.append(kwargs)
        assert text in kwargs["messages"][0]["content"]
        if len(calls) == 1:
            assert "skill_mentions" not in kwargs["response_format"]["json_schema"]["schema"]["properties"]
            return json.dumps({"name": "测试", "skills": ["Python"]})
        raise RuntimeError("LLM output truncated")
    result = extract_canonical_profile(SimpleNamespace(chat=chat), text)
    assert result["name"] == "测试"
    assert result["raw_text_chars"] == len(text)
    assert "error" not in result
    assert result["entity_linking"]["status"] == "unresolved"
    assert result["entity_linking"]["next_action"] == "retry_entity_linking"
    assert len(calls) == 2


def test_structure_call_preserves_real_usage_and_finish_metadata():
    from app.pipeline.entity_extractor.resume_structure import extract_canonical_profile
    model = SimpleNamespace(chat_with_metadata=lambda **kw: SimpleNamespace(
        text=json.dumps({"name": "测试"}), usage={"completion_tokens": 321},
        finish_reason="stop", provider="test", model="test-model"))
    result = extract_canonical_profile(model, "测试")
    assert result["structure_calls"][0]["usage"]["completion_tokens"] == 321
    assert result["structure_calls"][0]["finish_reason"] == "stop"


def test_primary_structure_truncation_never_returns_a_partial_success():
    from app.pipeline.entity_extractor.resume_structure import extract_canonical_profile, validate_structure_truth
    def chat(**kwargs):
        raise RuntimeError("LLM output truncated")
    text = "第一页。第二页。尾页。"
    result = extract_canonical_profile(SimpleNamespace(chat=chat), text)
    assert result["raw_text_chars"] == len(text)
    assert result["next_action"] == "retry_extraction"
    assert result["structure_calls"][0]["status"] == "failed"
    assert validate_structure_truth(result)["ok"] is False


def test_optional_extractor_uses_narrow_schema_and_preserves_tail_evidence():
    from app.pipeline.entity_extractor.llm_skill_extractor import LLMSkillExtractor
    text = "背景" * 2000 + "用Python开发"
    def chat(**kwargs):
        assert text in kwargs["messages"][0]["content"]
        schema = kwargs["response_format"]["json_schema"]["schema"]
        assert set(schema["$defs"]["SkillMention"]["properties"]) == {"name", "source_segment_index"}
        return json.dumps({"mentions": [{"name": "Python", "source_segment_index": 0}]})
    extractor = LLMSkillExtractor()
    extractor._llm_client = SimpleNamespace(chat=chat)
    result = extractor.extract_mentions(text)
    assert result["mentions"][0]["evidence"] == text
    assert result["call"]["input_chars"] == len(text)


def test_invalid_optional_quote_is_explicit_partial_not_invented_evidence():
    from app.pipeline.entity_extractor.llm_skill_extractor import LLMSkillExtractor
    extractor = LLMSkillExtractor()
    extractor._llm_client = SimpleNamespace(chat=lambda **kw: json.dumps({"mentions": [
        {"name": "Python", "source_segment_index": 0},
        {"name": "Java", "source_segment_index": 99}]}))
    result = extractor.extract_mentions("用Python开发")
    assert result["status"] == "partial"
    assert [x["name"] for x in result["mentions"]] == ["Python"]
    assert result["issues"] == [{"index": 1, "reason": "invalid_source_segment"}]


def test_profile_adapter_does_not_silently_clip_project_facts():
    from app.pipeline.entity_extractor.resume_structure import _normalize_profile
    description = "已验证的项目事实" * 100
    result = _normalize_profile({"projects": [{"name": "项目", "desc": description}]})
    assert result["projects"][0]["desc"] == description
