"""Model-reviewed is not human-approved; evidence and audit are reproducible."""
import copy
import json
import os
from types import SimpleNamespace

import pytest

from app.services.job_catalog_review import review_record, save_review
from app.services.job_publication import publish_job


def assessment():
    evidence = {name: {"status": "not_stated", "source_field": None,
                       "explanation": "Not stated; cannot infer no requirement"}
                for name in ("position_name", "degree_requirement", "major_requirements",
                             "major_strict", "required_certs", "required_skills")}
    evidence["position_name"] = {"status": "explicit", "source_field": "jobname",
                                 "explanation": "Exact source title"}
    return {"job_profile": {"degree_requirement": None, "major_requirements": [],
                            "major_strict": False, "required_certs": []},
            "required_skills": [], "field_evidence": evidence,
            "uncertainties": ["Requirements incomplete"], "unsupported_requirements": []}


class LLM:
    def __init__(self, output):
        self.output = output
        self.messages = None

    def chat_with_metadata(self, **kwargs):
        self.messages = kwargs["messages"]
        assert kwargs["response_format"]["type"] == "json_schema"
        return SimpleNamespace(text=json.dumps(self.output), model="test-only-model", provider="test-only",
                               usage={"total_tokens": 10}, finish_reason="stop")


def make_review(output=None, record=None):
    llm = LLM(assessment() if output is None else output)
    value = review_record(record or {"jobid": "test_only", "jobname": "Synthetic audit test",
                                     "entreq": "No complete requirements supplied",
                                     "enddate": "2099-12-31 23:59:00"},
                          feed_sha256="test-only-digest", fetched_at="2026-10-09T00:00:00Z", llm=llm)
    return value, llm


def test_unknown_requirements_are_audited_not_approved():
    review, _ = make_review()
    assert review["actor"] == "model" and review["status"] == "blocked"
    assert review["scoring_readiness"] is False and review["publication_authorized"] is False
    assert "missing:degree_requirement" in review["blockers"]
    assert review["candidate_snapshot"]["review_state"] == "pending"
    assert review["candidate_snapshot"]["review_actor"] == "model"
    with pytest.raises(ValueError, match="未经人工批准"):
        publish_job(None, job_portrait_id="test_only", version="v1", snapshot=review["candidate_snapshot"])


def test_full_record_conditions_are_sent_without_truncation():
    record = {"jobid": "test_only", "jobname": "Synthetic audit test", "entreq": "x" * 90000,
              "late_condition": "RESIDENCY REQUIREMENT AT END", "enddate": "2099-12-31 23:59:00"}
    review, llm = make_review(record=record)
    assert "RESIDENCY REQUIREMENT AT END" in llm.messages[-1]["content"]
    assert review["source_record"] == record


def test_unsupported_equivalent_degree_is_preserved_and_blocks_scoring():
    output = assessment()
    output["field_evidence"]["degree_requirement"] = {
        "status": "unsupported", "source_field": "entreq", "explanation": "Alternative qualification"}
    output["unsupported_requirements"] = ["Equivalent qualification cannot be mapped"]
    review, _ = make_review(output, {"jobid": "test_only", "jobname": "Synthetic audit test",
                                    "entreq": "Bachelor degree or equivalent", "enddate": "2099-12-31 23:59:00"})
    assert "unsupported_requirements" in review["blockers"]
    assert review["assessment"]["field_evidence"]["degree_requirement"]["status"] == "unsupported"
    assert review["candidate_snapshot"]["job_profile"]["degree_requirement"] is None


@pytest.mark.parametrize("fault", ["wrong_evidence_field", "extra_field", "wrong_type"])
def test_invalid_model_output_remains_auditable_and_cannot_supply_snapshot(fault):
    output = assessment()
    if fault == "wrong_evidence_field":
        output["field_evidence"]["position_name"]["source_field"] = "entreq"
    elif fault == "extra_field":
        output["actor"] = "human"
    else:
        output["job_profile"]["major_strict"] = "false"
    review, _ = make_review(output)
    assert review["status"] == "blocked"
    assert any(item.startswith("invalid_model_assessment:") for item in review["blockers"])
    assert "candidate_snapshot" not in review
    assert json.loads(review["model_output"]) == output


def test_non_contiguous_requirement_clauses_are_bound_to_exact_source_field():
    output = assessment()
    output["required_skills"] = [{"name": "communication", "level": "必须"}]
    output["field_evidence"]["required_skills"] = {
        "status": "explicit", "source_field": "entreq", "explanation": "Clause f is mandatory"}
    record = {"jobid": "test_only", "jobname": "Synthetic audit test",
              "entreq": "(e) English ability;\n(f) Good communication skills;\n(g) Teamwork ability.",
              "enddate": "2099-12-31 23:59:00"}
    review, _ = make_review(output, record)
    assert review["assessment"]["field_evidence"]["required_skills"]["source_field"] == "entreq"
    evidence = review["candidate_snapshot"]["field_evidence"]["required_skills"]
    assert evidence["excerpt"] == record["entreq"]
    assert evidence["excerpt"] in review["source_text"]
    assert review["candidate_snapshot"]["source_record_id"] == record["jobid"]
    assert review["candidate_snapshot"]["source_record_sha256"] == review["source_record_sha256"]
    assert review["status"] == "blocked" and review["publication_authorized"] is False


def test_missing_source_field_cannot_be_fabricated():
    output = assessment()
    output["field_evidence"]["required_skills"] = {
        "status": "explicit", "source_field": "entreq", "explanation": "Invented requirement"}
    review, _ = make_review(output, {"jobid": "test_only", "jobname": "Synthetic audit test",
                                    "enddate": "2099-12-31 23:59:00"})
    assert "invalid_model_assessment:invalid_source_field:required_skills" in review["blockers"]
    assert "candidate_snapshot" not in review


def test_candidate_source_can_be_reverified_against_exact_official_record(monkeypatch):
    import backend.scripts.mysql.verify_hk_csb_source as verifier

    record = {"jobid": "test_only", "jobname": "Synthetic audit test",
              "entreq": "Good communication skills", "enddate": "2099-12-31 23:59:00"}
    review, _ = make_review(record=record)
    body = json.dumps({"common": [{"vacancies": [record]}]}).encode("utf-8")
    monkeypatch.setattr(verifier.requests, "get", lambda *args, **kwargs:
                        SimpleNamespace(content=body, raise_for_status=lambda: None))
    verified = verifier.verify(review["candidate_snapshot"])
    assert verified["current"] is True and verified["reason"] == "verified_unchanged"


class AuditDB:
    def execute(self, sql, params=None, commit=False):
        assert "job_catalog_review" in sql and "job_catalog_publication" not in sql
        self.row = {"review_json": params["payload"], "content_sha256": params["digest"]}

    def fetch_all(self, sql, params=None):
        return [self.row]


def test_review_persistence_reads_full_payload_back():
    review, _ = make_review()
    result = save_review(AuditDB(), review)
    assert result["verified"] is True and result["publication_authorized"] is False
    review["actor"] = "human"
    with pytest.raises(ValueError, match="impersonate"):
        save_review(AuditDB(), review)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="explicit MySQL opt-in")
def test_live_model_review_audit_roundtrip_and_immutable_id():
    from app.services.db.mysql_client import get_mysql_client
    db = get_mysql_client()
    review, _ = make_review()
    before = db.fetch_all("SELECT COUNT(*) AS n FROM job_catalog_publication")[0]["n"]
    try:
        result = save_review(db, review)
        assert result["verified"] is True
        with pytest.raises(Exception):
            save_review(db, copy.deepcopy(review))
        assert db.fetch_all("SELECT COUNT(*) AS n FROM job_catalog_publication")[0]["n"] == before
    finally:
        db.execute("DELETE FROM job_catalog_review WHERE review_id=:rid",
                   {"rid": review["review_id"]}, commit=True)
