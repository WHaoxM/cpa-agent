"""Publication trust, hard-constraint delegation and immutable report evidence."""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import pipeline
from app.services.job_publication import content_sha256, publish_job, read_published_job
from app.services import verified_match_report as service
from backend.tests.test_match_report_source_gate import SourceDB


def snapshot():
    source = ("Test-only role. Required Python skill. Bachelor's degree required. "
              "No major restriction. No certificates required. Synthetic fixture, not a real job.")
    return {"position_name": "Test-only role", "source_uri": "https://example.com/test-only",
            "source_text": source, "source_text_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "source_fetched_at": "2026-10-08T00:00:00Z", "source_license": "test-fixture",
            "field_evidence": {
                "position_name": {"status": "explicit", "excerpt": "Test-only role"},
                "degree_requirement": {"status": "explicit", "excerpt": "Bachelor's degree required"},
                "major_requirements": {"status": "explicit", "excerpt": "No major restriction"},
                "major_strict": {"status": "explicit", "excerpt": "No major restriction"},
                "required_certs": {"status": "explicit", "excerpt": "No certificates required"},
                "required_skills": {"status": "explicit", "excerpt": "Required Python skill"},
            }, "review_state": "approved", "reviewed_by": "test-only",
            "reviewed_at": "2026-10-09T00:00:00Z", "required_skills": [{"name": "Python", "level": "必须"}],
            "job_profile": {"degree_requirement": "本科", "major_requirements": [],
                            "major_strict": False, "required_certs": []}}


class DB(SourceDB):
    def __init__(self):
        super().__init__()
        self.publication = {"id": 1, "job_portrait_id": "job_1", "publication_version": "v1",
                            "snapshot_json": snapshot(), "content_sha256": content_sha256(snapshot()),
                            "status": "published"}
        self.match = None
        self.report = None

    def fetch_all(self, sql, params=None):
        if "FROM job_catalog_publication" in sql:
            return [self.publication] if self.publication else []
        if "FROM match_result" in sql:
            return [self.match] if self.match else []
        if "FROM career_report" in sql:
            return [self.report] if self.report else []
        return super().fetch_all(sql, params)

    def execute(self, sql, params=None, commit=False):
        if "INSERT INTO match_result" in sql:
            self.match = {"id": 7, "student_id": params["sid"], "job_portrait_id": params["jid"],
                          "result_json": json.loads(params["payload"])}
        if "INSERT INTO career_report" in sql:
            self.report = {"report_id": params["rid"], "student_id": params["sid"], "status": "partial",
                           "report_json": json.loads(params["payload"])}
        return super().execute(sql, params, commit)


@pytest.mark.parametrize("level", ["L3_run_match", "L4_generate_report"])
def test_missing_confirmation_is_business_block_not_execution_failure(monkeypatch, level):
    db = DB()
    original = db.fetch_all
    db.fetch_all = lambda sql, params=None: [] if "FROM student_profile" in sql else original(sql, params)
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    outcome = pipeline.PipelineRunner().run(level, {"student_id": "owner", "job_id": "job_1"})
    assert outcome["status"] == "partial"
    assert outcome["reason"] == "confirmed_resume_unavailable"
    assert outcome["next_action"] == "confirm_resume"
    assert not outcome.get("match_id") and not outcome.get("report_id")
    assert db.match is None and db.report is None


@pytest.fixture
def scored(monkeypatch):
    db = DB()
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    from app.pipeline.entity_extractor import matcher
    def score(skills, requirements, student, job):
        assert student["_resume_confirmation"]["task_id"] == "confirm_1"
        assert job == snapshot()["job_profile"]
        assert requirements == snapshot()["required_skills"]
        return {"total_score": 99, "d1_score": 0, "d2_score": 99, "d3_score": 99,
                "d4_score": 99, "skill_coverage": 1, "match_result": False, "d1_pass": False,
                "match_level": "不匹配", "missing_skills": []}
    monkeypatch.setattr(matcher, "calculate_match_score", score)
    monkeypatch.setattr(service, "score_policy", lambda: {"rules": "test", "implementation_sha256": "test"})
    return db


def test_l3_l4_share_verified_sources_and_ignore_forged_body(scored):
    runner = pipeline.PipelineRunner()
    match = runner.run("L3_run_match", {"student_id": "owner", "job_id": "job_1", "job_skills": ["forged"]})
    assert match["status"] == "partial" and match["match_id"] == "7"
    assert match["score_kind"] == "policy_estimate"
    assert match["is_matched"] is False  # high score cannot bypass scorer's hard constraints
    report = runner.run("L4_generate_report", {"student_id": "owner", "job_id": "job_1", "report_id": "forged"})
    assert report["status"] == "partial" and report["verified"] is True
    assert report["report_id"] != "forged"
    assert scored.report["report_json"]["report_id"] == report["report_id"]
    assert scored.report["report_json"]["match_snapshot"] == scored.match["result_json"]
    assert scored.report["report_json"]["missing_sections"]


@pytest.mark.parametrize("fault", ["owner", "tamper", "task", "version", "missing"])
def test_l4_rejects_stale_or_unowned_match(scored, fault):
    runner = pipeline.PipelineRunner()
    result = runner.run("L3_run_match", {"student_id": "owner", "job_id": "job_1"})
    if fault == "owner": scored.match["student_id"] = "other"
    if fault == "tamper": scored.match["result_json"]["total_score"] = 100
    if fault == "task": scored.tasks[result["task_id"]]["status"] = "failed"
    if fault == "version": scored.publication["publication_version"] = "v2"
    if fault == "missing": scored.match = None
    result = runner.run("L4_generate_report", {"student_id": "owner", "job_id": "job_1"})
    assert result["reason"] == "verified_match_unavailable"
    assert not result.get("report_id") and scored.report is None


def test_publication_tamper_and_revocation():
    db = DB()
    db.publication["snapshot_json"]["position_name"] = "tampered"
    with pytest.raises(ValueError, match="摘要"):
        read_published_job(db, "job_1")
    db.publication["status"] = "revoked"
    assert read_published_job(db, "job_1") is None


def test_publication_missing_hard_constraints_never_written():
    db = DB()
    data = snapshot()
    del data["job_profile"]["degree_requirement"]
    with pytest.raises(ValueError, match="硬约束"):
        publish_job(db, job_portrait_id="job_1", version="v2", snapshot=data)
    assert not db.writes


def test_unstated_degree_never_defaults_to_no_requirement(scored):
    data = snapshot()
    data["job_profile"]["degree_requirement"] = None
    data["field_evidence"]["degree_requirement"] = {"status": "not_stated", "excerpt": None}
    scored.publication["snapshot_json"] = data
    scored.publication["content_sha256"] = content_sha256(data)
    result = pipeline.PipelineRunner().run("L3_run_match", {"student_id": "owner", "job_id": "job_1"})
    assert result["status"] == "partial"
    assert result["reason"] == "catalog_requirements_unknown"
    assert "degree_requirement" in result["missing_job_fields"]
    assert scored.match is None


def test_source_text_digest_and_field_excerpt_are_checked_before_publication():
    db = DB()
    data = snapshot()
    data["source_text"] += " tamper"
    with pytest.raises(ValueError, match="SHA-256"):
        publish_job(db, job_portrait_id="job_1", version="v3", snapshot=data)
    data = snapshot()
    data["field_evidence"]["required_skills"]["excerpt"] = "unseen Python requirement"
    with pytest.raises(ValueError, match="证据不在来源文本"):
        publish_job(db, job_portrait_id="job_1", version="v4", snapshot=data)
    assert not db.writes


def test_pending_real_source_cannot_enter_trusted_catalog():
    db = DB()
    data = snapshot()
    data["review_state"] = "pending"
    with pytest.raises(ValueError, match="未经人工批准"):
        publish_job(db, job_portrait_id="job_1", version="v5", snapshot=data)
    assert not db.writes


def test_official_feed_candidate_is_reproducible_but_not_publishable(monkeypatch):
    from backend.scripts.mysql.verify_hk_csb_source import verify
    import backend.scripts.mysql.verify_hk_csb_source as probe
    path = Path(__file__).parent / "fixtures/reviewed_sources/hk_csb_50368_snapshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    source = (path.parent / "hk_csb_50368_source.txt").read_text(encoding="utf-8")
    assert source == data["source_text"]
    record = {"jobid": 50368, "jobname": data["position_name"],
              "entreq": source, "enddate": "2099-12-31 23:59:00"}
    # Pin this deliberately synthetic network fixture independently of the
    # real captured source record; changing a real record requires re-review.
    data["source_record_sha256"] = content_sha256(record)
    response = SimpleNamespace(content=json.dumps({"common": [{"vacancies": [record]}]}).encode(),
                               raise_for_status=lambda: None)
    monkeypatch.setattr(probe.requests, "get", lambda url, timeout: response)
    checked = verify(data)
    assert checked["current"] is True and checked["excerpts_match"] is True
    db = DB()
    with pytest.raises(ValueError, match="未经人工批准"):
        publish_job(db, job_portrait_id="hk_csb_50368", version="source-2026-10-09", snapshot=data)
    assert not db.writes


def test_l3_readback_failure_never_completed(scored, monkeypatch):
    original = scored.fetch_all
    monkeypatch.setattr(scored, "fetch_all", lambda sql, params=None:
                        [] if "FROM match_result" in sql else original(sql, params))
    with pytest.raises(RuntimeError, match="回读"):
        pipeline.PipelineRunner().run("L3_run_match", {"student_id": "owner", "job_id": "job_1"})
    assert any(row["status"] == "failed" for row in scored.tasks.values())


def test_l3_missing_confirmed_semantic_evidence_never_writes_score(scored, monkeypatch):
    from app.pipeline.entity_extractor import matcher
    monkeypatch.setattr(matcher, "calculate_match_score", lambda *_: {
        "status": "partial", "total_score": None, "d1_score": 0, "d2_score": 99,
        "d3_score": None, "d4_score": 99, "missing_fields": ["teamwork"],
        "match_result": False, "d1_pass": False,
    })
    result = pipeline.PipelineRunner().run("L3_run_match", {"student_id": "owner", "job_id": "job_1"})
    assert result["status"] == "partial"
    assert result["reason"] == "scoring_evidence_incomplete"
    assert result["match_id"] is None
    assert result["resource_type"] == "pipeline_task"
    assert scored.match is None
    assert not any("INSERT INTO match_result" in sql for sql, _ in scored.writes)


def test_page_and_agent_share_persisted_matching_and_partial_report(scored, monkeypatch):
    from flask import Flask, g
    from app.api import profile as api
    from app.config import Config
    from app.services.tools.pipeline_run import PipelineRunTool
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    @app.before_request
    def principal():
        g.cpa_principal = {"principal_id": "owner", "student_id": "owner"}
    app.register_blueprint(api.profile_bp, url_prefix="/api/profile")
    response = app.test_client().post("/api/profile/student/pipeline", json={
        "pipeline": "L3_run_match", "job_portrait_id": "job_1", "student_id": "forged"})
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    result = response.get_json()["data"]
    assert result["match_id"] == "7" and result["status"] == "partial"
    assert scored.tasks[result["task_id"]]["student_id"] == "owner"
    tool = PipelineRunTool()
    tool._mysql = scored
    outcome = json.loads(tool.execute(pipeline="L4_generate_report", student_id="owner", params={"job_id": "job_1"}))
    assert outcome["status"] == "partial"
    assert outcome["result"]["report_id"] == scored.report["report_id"]
    assert outcome["result"]["sources"]["match_id"] == "7"
