"""Source gates must run before matching/report side effects (no live services)."""
import copy
import json

import pytest
from flask import Flask

from app.services import pipeline


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    from app.pipeline.entity_extractor import matcher, graph_storage, report_generator
    def forbidden(*args, **kwargs):
        raise AssertionError("Source gate must run before external computation")
    monkeypatch.setattr(matcher, "calculate_match_score", forbidden)
    monkeypatch.setattr(graph_storage, "GraphStorage", forbidden)
    monkeypatch.setattr(report_generator.ReportGenerator, "generate_career_report", forbidden)


class SourceDB:
    def __init__(self):
        self.writes = []
        self.profile = {"skills": ["Python"], "_resume_confirmation": {
            "student_id": "owner", "task_id": "confirm_1"}}
        self.portrait = {"id": 41, "student_id": "owner", "target_job_id": "job_1",
                         "portrait_json": {"source_confirmation_task_id": "confirm_1",
                                           "source_portrait_task_id": "portrait_task_1"}}
        self.tasks = {
            "confirm_1": {"task_id": "confirm_1", "student_id": "owner",
                "pipeline": "L0_parse_resume", "status": "completed", "result_json": {
                    "confirmed": True, "candidate_profile": {"skills": ["Python"]}}},
            "portrait_task_1": {"task_id": "portrait_task_1", "student_id": "owner",
                "pipeline": "L2_generate_portrait", "status": "completed", "result_json": {
                    "portrait_id": "41", "portrait": copy.deepcopy(self.portrait["portrait_json"])}}}
        self.catalog = {"portrait_id": "job_1", "position_name": "Python developer",
                        "portrait_json": {"required_skills": ["Python"], "published": True}}

    def fetch_all(self, sql, params=None):
        if "FROM job_catalog_publication" in sql:
            return []
        if "FROM student_profile" in sql:
            return [{"profile_json": self.profile}]
        if "FROM pipeline_task" in sql:
            row = self.tasks.get(params["task_id"])
            return [row] if row else []
        if "FROM student_portrait_4d" in sql:
            return [self.portrait] if self.portrait else []
        if "FROM job_portrait_4d" in sql:
            return [self.catalog] if self.catalog else []
        raise AssertionError(f"Unexpected source query: {sql}")

    def execute(self, sql, params=None, commit=False):
        self.writes.append((sql, params))
        if "INSERT INTO pipeline_task" in sql:
            self.tasks[params["task_id"]] = dict(params)
        elif "UPDATE pipeline_task" in sql:
            self.tasks.setdefault(params["task_id"], {}).update(params)
        return []


@pytest.mark.parametrize("level", ["L3_run_match", "L4_generate_report"])
def test_catalog_row_and_json_published_flag_are_not_publication_authority(monkeypatch, level):
    db = SourceDB()
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    result = pipeline.PipelineRunner().run(level, {
        "student_id": "owner", "job_portrait_id": "job_1", "report_id": "forged",
        "job_skills": [{"name": "forged"}], "published": True})
    assert result["status"] == "partial"
    assert result["reason"] == "catalog_publication_unavailable"
    assert result["next_action"] == "publish_verified_job_catalog"
    assert result["source_confirmation_task_id"] == "confirm_1"
    assert result["source_portrait_id"] == "41"
    assert result["source_portrait_task_id"] == "portrait_task_1"
    assert not result.get("match_id") and not result.get("report_id")
    assert result["resource_type"] == "pipeline_task"
    assert result["resource_id"] == result["task_id"]
    assert db.tasks[result["task_id"]]["status"] == "partial"
    assert not any("INSERT INTO match_result" in sql or "INSERT INTO career_report" in sql
                   for sql, _ in db.writes)


@pytest.mark.parametrize("fault", ["missing", "owner", "confirmation", "task", "payload", "target"])
def test_l3_refuses_unverified_or_stale_l2_before_catalog(fault):
    db = SourceDB()
    if fault == "missing": db.portrait = None
    elif fault == "owner": db.portrait["student_id"] = "other"
    elif fault == "confirmation": db.portrait["portrait_json"]["source_confirmation_task_id"] = "old"
    elif fault == "task": db.tasks["portrait_task_1"]["status"] = "failed"
    elif fault == "payload": db.portrait["portrait_json"]["summary"] = {"competitiveness_score": 100}
    elif fault == "target": db.portrait["target_job_id"] = "other_job"
    result = pipeline.L3RunMatchPipeline("task_test", {
        "student_id": "owner", "job_id": "job_1"}, db).run()
    assert result["status"] == "partial"
    assert result["reason"] == "confirmed_portrait_unavailable"
    assert result["next_action"] == "generate_portrait"
    assert not result.get("match_id")


def test_page_and_agent_runner_share_catalog_block(monkeypatch):
    from app.api import profile as api
    from app.config import Config
    db = SourceDB()
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "owner")
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    app = Flask(__name__)
    app.register_blueprint(api.profile_bp, url_prefix="/api/profile")
    response = app.test_client().post("/api/profile/student/pipeline", json={
        "pipeline": "L3_run_match", "job_portrait_id": "job_1", "student_id": "intruder"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["status"] == "partial"
    assert payload["data"]["reason"] == "catalog_publication_unavailable"
    assert db.tasks[payload["data"]["task_id"]]["student_id"] == "owner"


@pytest.mark.parametrize("level", ["L3_run_match", "L4_generate_report"])
def test_agent_tool_reports_only_the_blocked_task_artifact(level):
    from app.services.tools.pipeline_run import PipelineRunTool
    db = SourceDB()
    tool = PipelineRunTool()
    tool._mysql = db
    result = json.loads(tool.execute(pipeline=level, student_id="owner", params={
        "job_portrait_id": "job_1", "report_id": "forged"}))
    assert result["status"] == "partial"
    assert result["result"]["reason"] == "catalog_publication_unavailable"
    assert db.tasks[result["task_id"]]["status"] == "partial"
    assert all(a["resource_type"] == "pipeline_task" and a["status"] == "partial"
               for a in result["artifact_candidates"])
    assert not result["result"].get("report_id") and not result["result"].get("match_id")
