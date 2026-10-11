import json
from io import BytesIO

import pytest
from flask import g

from app import create_app
from app.api import report as report_api
from app.api import profile as profile_api
from app.api import pipeline as pipeline_api
from app.api import ai_analysis as ai_api
from app.api import learning as learning_api
from app.config import Config
from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.pre_tool_use import GateContext, pre_tool_use
from app.services.agent_runtime.tool_result_channels import build_ui_view
from app.services.agent_runtime.tool_egress import sanitize_tool_call_record
from app.services.agent_runtime.artifact_committer import ArtifactCommitter
from app.services.tools.pipeline_run import PipelineRunTool
from app.services.tools.skill_tools import PortraitGenerateTool, ReportGenerateTool


def test_pipeline_json_identity_is_rewritten_before_business_execution(monkeypatch):
    ctx = GateContext(interaction_mode=InteractionMode.AGENT, subagent="profile",
                      student_id="owner_A", session_id="session_A")
    gate = pre_tool_use("pipeline_run", {
        "pipeline": "L0_parse_resume",
        "params": json.dumps({"student_id": "victim_B", "nested": {"user_id": "victim_B"},
                              "text": "Python"}),
    }, ctx)
    assert gate.allowed
    assert gate.rewritten
    assert json.loads(gate.args["params"])["student_id"] == "owner_A"
    assert json.loads(gate.args["params"])["nested"]["user_id"] == "owner_A"

    tool = PipelineRunTool()
    seen = {}
    monkeypatch.setattr(tool, "_run_pipeline_sync", lambda pipeline, params: seen.update(params) or "ok")
    assert tool.execute(**gate.args) == "ok"
    assert seen["student_id"] == "owner_A"


def test_pipeline_false_result_never_completes_or_commits_completed_candidate(monkeypatch):
    tool = PipelineRunTool()

    class FakeMySQL:
        def __init__(self):
            self.updates = []

        def execute(self, sql, params=None, commit=False):
            self.updates.append((sql, params or {}))

    fake = FakeMySQL()
    tool._mysql = fake
    monkeypatch.setattr(tool, "_execute_pipeline", lambda pipeline, params, task_id: {
        "success": False, "summary": "解析失败"
    })
    result = json.loads(tool.execute(pipeline="L0_parse_resume", params='{"text":"bad"}', student_id="owner_A"))
    assert result["status"] == "failed"
    assert all(c["status"] != "completed" for c in result["artifact_candidates"])
    assert not any("completed_at" in sql and params.get("status") == "completed" for sql, params in fake.updates)
    assert any(params.get("status") == "failed" for _, params in fake.updates)


def test_pipeline_rejects_foreign_resume_path(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    tool = PipelineRunTool()
    result = tool.execute(
        pipeline="L0_parse_resume",
        params=json.dumps({"file_path": str(tmp_path / "victim_B" / "resume.pdf")}),
        student_id="owner_A",
    )
    assert "不属于当前 Principal" in result


def test_tool_failure_and_draft_do_not_render_as_completed():
    failed = build_ui_view("pipeline_run", {"success": False, "summary": "operation failed"})
    assert failed["status"] == "error"
    assert "已完成" not in failed["summary"]
    partial = build_ui_view("portrait_generate", {"status": "partial", "verified": False,
                                                   "summary": "画像草稿已计算，尚未持久化"})
    assert partial["status"] == "partial"
    egress = sanitize_tool_call_record({"tool_name": "pipeline_run", "status": "error",
                                        "summary": "operation failed"})
    assert "已完成" not in egress["summary"]


def test_artifact_without_authoritative_status_is_unverified():
    writes = []

    class FakeMySQL:
        def execute(self, sql, params=None, commit=False):
            writes.append(params or {})

    committer = ArtifactCommitter(mysql=FakeMySQL())
    committer._schema_ready = True
    refs = committer.commit_candidates([{
        "kind": "pipeline_task", "resource_type": "pipeline_task",
        "resource_id": "task_1", "title": "Task",
    }, {
        "kind": "career_report", "resource_type": "career_report",
        "resource_id": "report_unchecked", "title": "Unchecked", "status": "completed",
    }], student_id="owner_A", session_id="session_A")
    assert refs[0]["status"] == "unverified"
    assert writes[0]["status"] == "unverified"
    assert refs[1]["status"] == "unverified"
    public = sanitize_tool_call_record({"tool_name": "pipeline_run", "artifact_refs": [{
        "artifact_id": "art_1", "kind": "pipeline_task"
    }]})
    assert public["artifact_refs"][0]["status"] == "unverified"


def test_orphan_4d_row_cannot_mark_profile_complete(monkeypatch):
    """A stale score without its authoritative student profile is not a portrait."""
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "student_001", "student_id": "student_001"}

    class OrphanOnlyMySQL:
        def execute(self, sql, params=None):
            assert "JOIN student_profile" in sql
            assert params["student_id"] == "student_001"
            # The legacy 4d row exists, but the joined authoritative profile does not.
            return []

    monkeypatch.setattr(profile_api, "get_mysql_client", lambda: OrphanOnlyMySQL())
    response = app.test_client().get("/api/profile/student/student_001/4d")
    assert response.status_code == 404
    assert response.get_json()["success"] is False


def test_portrait_and_report_drafts_use_trusted_student_not_model_name(monkeypatch):
    from app.pipeline.entity_extractor import portrait_generator, l4_report_generator

    seen = {}

    def fake_portrait(*, student_id, skills):
        seen["portrait_student_id"] = student_id
        return {"student_id": student_id}

    def fake_report(**kwargs):
        seen["report_student_id"] = kwargs["student_id"]
        return {"summary": {"conclusion": "draft"}, "recommendations": []}

    monkeypatch.setattr(portrait_generator, "generate_student_portrait", fake_portrait)
    monkeypatch.setattr(l4_report_generator, "generate_full_report", fake_report)
    portrait = PortraitGenerateTool().execute(entity_type="student", skills=[],
                                               name="victim_B", student_id="owner_A")
    report = ReportGenerateTool().execute(student_skills=[], job_skills=[], student_id="owner_A")
    assert seen == {"portrait_student_id": "owner_A", "report_student_id": "owner_A"}
    assert portrait["status"] == report["status"] == "partial"
    assert "report_id" not in report


def test_report_generate_uses_principal_and_returns_runner_record_ids(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    from app.services import pipeline as service_pipeline

    seen = {}

    class FakeRunner:
        def run(self, pipeline, params):
            seen.update(params)
            return {"report_id": "report_real", "task_id": "task_real", "status": "partial", "summary": "draft"}

    class FakeMySQL:
        def execute(self, sql, params=None):
            assert params["student_id"] == "owner_A"
            return [{"status": "partial"}]

    monkeypatch.setattr(service_pipeline, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(report_api, "get_mysql_client", FakeMySQL)
    response = app.test_client().post("/api/report/generate", json={
        "student_id": "victim_B", "job_portrait_id": "job_1",
    })
    body = response.get_json()
    assert response.status_code == 200
    assert seen["student_id"] == "owner_A"
    assert body["data"]["report_id"] == "report_real"
    assert body["data"]["task_id"] == "task_real"
    assert body["data"]["status"] == "partial"
    assert body["success"] is False


def test_report_generate_without_authoritative_report_id_returns_task_only(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    from app.services import pipeline as service_pipeline

    class FakeRunner:
        def run(self, pipeline, params):
            return {"task_id": "task_real", "status": "partial", "summary": "缺岗位来源"}

    class FakeMySQL:
        def execute(self, sql, params=None):
            assert params == {"task_id": "task_real", "student_id": "owner_A"}
            return [{"status": "partial"}]

    monkeypatch.setattr(service_pipeline, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(report_api, "get_mysql_client", FakeMySQL)
    response = app.test_client().post("/api/report/generate", json={"job_portrait_id": "job_1"})
    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is False
    assert body["data"]["status"] == "partial"
    assert body["data"]["task_id"] == "task_real"
    assert body["data"]["report_id"] is None


def test_report_generate_rejects_id_without_owned_persisted_rows(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    from app.services import pipeline as service_pipeline

    class FakeRunner:
        def run(self, pipeline, params):
            return {"report_id": "invented_report", "task_id": "invented_task", "status": "completed"}

    class FakeMySQL:
        def execute(self, sql, params=None):
            return []

    monkeypatch.setattr(service_pipeline, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(report_api, "get_mysql_client", FakeMySQL)
    response = app.test_client().post("/api/report/generate", json={"job_portrait_id": "job_1"})
    assert response.status_code == 500
    assert response.get_json()["success"] is False


def test_report_read_is_owner_scoped(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    class FakeMySQL:
        def __init__(self):
            self.query = None

        def execute(self, sql, params=None):
            self.query = (sql, params)
            return []

    fake = FakeMySQL()
    monkeypatch.setattr(report_api, "get_mysql_client", lambda: fake)
    response = app.test_client().get("/api/report/report_of_B")
    assert response.status_code == 404
    assert "cr.student_id = :student_id" in fake.query[0]
    assert fake.query[1]["student_id"] == "owner_A"


def test_profile_upload_returns_persisted_pipeline_task_id(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()
    from app.services import pipeline as service_pipeline

    class FakeRunner:
        def run(self, pipeline, params):
            return {"task_id": "task_persisted", "block_count": 3,
                    "status": "needs_input", "candidate_profile": {"name": "Test"},
                    "profile_ready": False, "student_id": params["student_id"]}

    monkeypatch.setattr(service_pipeline, "PipelineRunner", FakeRunner)
    response = app.test_client().post("/api/profile/student/upload", data={
        "file": (BytesIO(b"resume text"), "resume.txt")
    }, content_type="multipart/form-data")
    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["task_id"] == "task_persisted"
    assert body["data"]["status"] == "needs_input"
    assert body["data"]["profile_ready"] is False


def test_legacy_pipeline_trigger_binds_owner_and_demo_is_only_partial(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    app = create_app()
    writes = []

    class FakeMySQL:
        def execute_write(self, sql, params=None):
            writes.append((sql, params or {}))

    monkeypatch.setattr(pipeline_api, "get_mysql_client", FakeMySQL)
    response = app.test_client().post("/api/pipeline/trigger", json={
        "pipeline": "L0_parse_resume", "student_id": "victim_B", "resume_text": "Python"
    })
    assert response.status_code == 200
    assert writes[0][1]["student_id"] == Config.DEMO_STUDENT_ID
    assert response.get_json()["data"]["status"] == "partial"
    assert "status='partial'" in writes[1][0]


def test_growth_analysis_foreign_report_id_is_filtered_by_owner(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    reads = []

    def read_resource(_mysql, *, student_id, uri, section):
        reads.append((student_id, uri, section))
        if uri == "career://me/portrait":
            return {"success": True, "status": "completed", "published": True,
                    "uri": uri, "version": "portrait_A", "data": {"total_score": 75}}
        raise LookupError("not owned")

    monkeypatch.setattr(ai_api, "get_mysql_client", object)
    monkeypatch.setattr(
        "app.services.agent_runtime.resource_catalog.read_platform_resource", read_resource,
    )
    monkeypatch.setattr(ai_api, "_require_llm", lambda: pytest.fail("LLM must not run for foreign report"))
    response = app.test_client().post("/api/ai/growth-analysis", json={
        "userId": "victim_B", "reportIds": ["report_of_B"]
    })
    assert response.status_code == 409
    assert response.get_json()["reason"] == "requested_report_unavailable"
    assert reads == [
        ("owner_A", "career://me/portrait", "overview"),
        ("owner_A", "career://me/reports/report_of_B", "overview"),
    ]


def test_learning_recommend_and_progress_use_principal(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    calls = []

    class FakeMySQL:
        def execute(self, sql, params=None):
            calls.append((sql, params or {}))
            if "FROM course c JOIN chapter ch" in sql:
                return [{"course_id": params["course_id"], "chapter_id": params["chapter_id"]}]
            if "FROM learning_progress WHERE user_id" in sql:
                return [{"user_id": "owner_A", "course_id": "c1", "chapter_id": "1",
                         "progress": 0.5, "completed": 0}]
            return []

        def execute_write(self, sql, params=None):
            calls.append((sql, params or {}))

    monkeypatch.setattr(learning_api, "get_mysql_client", FakeMySQL)
    client = app.test_client()
    assert client.get("/api/learning/learning/progress/victim_B").status_code == 403
    response = client.get("/api/learning/recommend?student_id=victim_B")
    assert response.status_code == 200
    owner_queries = [params for sql, params in calls if "student_self_assessment" in sql]
    assert owner_queries and owner_queries[0]["student_id"] == "owner_A"
    response = client.post("/api/learning/learning/progress", json={
        "user_id": "victim_B", "course_id": "c1", "chapter_id": 1, "progress": 0.5,
    })
    assert response.status_code == 200
    assert calls[-1][1]["user_id"] == "owner_A"


def test_user_resource_apis_fail_closed_without_principal(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    client = create_app().test_client()
    assert client.get("/api/report/list").status_code == 401
    assert client.get("/api/pipeline/status/task_guess").status_code == 401
    assert client.post("/api/ai/growth-analysis", json={}).status_code == 401
    assert client.get("/api/learning/recommend?student_id=victim_B").status_code == 401
