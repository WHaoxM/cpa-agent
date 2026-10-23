"""Keep replay dependency outputs grounded; never seed a completed business artifact."""
import hashlib
import pytest

from backend.tests.manual.job_source_to_catalog_browser_probe import (
    ReplayResumeModel, candidate, decision_resume_fixtures,
)
from app.pipeline.entity_extractor.resume_structure import extract_canonical_profile, validate_structure_truth


@pytest.mark.parametrize("index,skills", [(0, ["Python"]), (1, ["Python", "SQL"])])
def test_reassessment_uses_full_document_specific_extraction(index, skills):
    fixtures = decision_resume_fixtures()
    calls = []
    model = ReplayResumeModel(fixtures, calls)
    text = fixtures[index][0]
    profile = extract_canonical_profile(model, text, link_entities=False)
    assert validate_structure_truth(profile)["ok"]
    assert profile["skills"] == skills
    assert len(profile["projects"]) == index + 1
    for fact in profile["semantic_facts"].values():
        assert fact["evidence"] in text
        assert fact["source_sha256"] == hashlib.sha256(text.strip().encode()).hexdigest()
    assert calls[0]["input_sha256"] == profile["source_sha256"]


def test_replay_model_rejects_unknown_input_instead_of_reusing_a_profile():
    model = ReplayResumeModel(decision_resume_fixtures(), [])
    with pytest.raises(ValueError, match="no fallback fixture"):
        model.chat_with_metadata(messages=[{"role": "user", "content": "unexpected resume"}])
    profile = extract_canonical_profile(model, "unexpected resume", link_entities=False)
    assert not validate_structure_truth(profile)["ok"]


def test_reassessment_job_adds_explicit_sql_without_changing_other_replays():
    assert [s["name"] for s in candidate("fixture")["required_skills"]] == ["Python"]
    job = candidate("fixture", include_sql=True)
    assert [s["name"] for s in job["required_skills"]] == ["Python", "SQL"]
    assert job["field_evidence"]["required_skills"]["excerpt"] in job["source_text"]
