"""Real-MySQL state-transition regression for the synchronous pipeline runner.

Run with CPA_TEST_LIVE_MYSQL=1. No model, Celery worker, or domain artifact is
created; UUID-scoped task and journal rows are removed after each assertion.
"""

import json
import os
import uuid

import pytest

from app.services.db.mysql_client import get_mysql_client
from app.services.pipeline import BasePipeline, PipelineStatus


class _StatusProbe(BasePipeline):
    def run(self):
        raise NotImplementedError


@pytest.fixture
def mysql_task():
    mysql = get_mysql_client()
    student_id = "status_cas_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:16]

    def create(status="pending"):
        mysql.execute(
            "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,params_json) "
            "VALUES (:task_id,'L0_parse_resume',:status,:student_id,'{}')",
            {"task_id": task_id, "student_id": student_id, "status": status},
            commit=True,
        )
        return _StatusProbe(task_id, {"student_id": student_id}, mysql)

    try:
        yield mysql, task_id, student_id, create
    finally:
        mysql.execute("DELETE FROM pipeline_task_event WHERE student_id=:student_id",
                      {"student_id": student_id}, commit=True)
        mysql.execute("DELETE FROM pipeline_task WHERE student_id=:student_id",
                      {"student_id": student_id}, commit=True)
        assert not mysql.fetch_all("SELECT task_id FROM pipeline_task WHERE student_id=:student_id",
                                   {"student_id": student_id})


def _events(mysql, task_id):
    return [row["status"] for row in mysql.fetch_all(
        "SELECT status FROM pipeline_task_event WHERE task_id=:task_id ORDER BY event_id",
        {"task_id": task_id},
    )]


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_terminal_task_cannot_restart_or_append_event(mysql_task):
    mysql, task_id, _, create = mysql_task
    probe = create("completed")
    before = _events(mysql, task_id)

    with pytest.raises(RuntimeError, match="状态转换"):
        probe.update_status(PipelineStatus.RUNNING)

    row = mysql.fetch_all("SELECT status,started_at FROM pipeline_task WHERE task_id=:task_id",
                          {"task_id": task_id})[0]
    assert row["status"] == "completed"
    assert row["started_at"] is None
    assert _events(mysql, task_id) == before


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_missing_task_cannot_claim_running(mysql_task):
    mysql, task_id, student_id, _ = mysql_task
    probe = _StatusProbe(task_id, {"student_id": student_id}, mysql)

    with pytest.raises(RuntimeError, match="任务不存在或状态已改变"):
        probe.update_status(PipelineStatus.RUNNING)

    assert _events(mysql, task_id) == []


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
@pytest.mark.parametrize("terminal", [PipelineStatus.COMPLETED, PipelineStatus.NEEDS_INPUT,
                                      PipelineStatus.PARTIAL, PipelineStatus.FAILED])
def test_pending_running_terminal_is_atomic_and_cannot_repeat(mysql_task, terminal):
    mysql, task_id, _, create = mysql_task
    probe = create()
    probe.update_status(PipelineStatus.RUNNING)
    with pytest.raises(RuntimeError, match="状态转换"):
        _StatusProbe(task_id, probe.params, mysql).update_status(PipelineStatus.RUNNING)
    assert _events(mysql, task_id) == ["pending", "running"]
    payload = {"summary": "synthetic probe"}
    probe.update_status(terminal, result=payload, error="synthetic failure")

    row = mysql.fetch_all(
        "SELECT status,started_at,completed_at,result_json,error_message "
        "FROM pipeline_task WHERE task_id=:task_id", {"task_id": task_id},
    )[0]
    assert row["status"] == terminal.value
    assert row["started_at"] is not None
    assert (row["completed_at"] is not None) is (terminal == PipelineStatus.COMPLETED)
    if terminal == PipelineStatus.FAILED:
        assert row["error_message"] == "synthetic failure"
    else:
        stored = row["result_json"]
        assert (json.loads(stored) if isinstance(stored, str) else stored) == payload
    assert _events(mysql, task_id) == ["pending", "running", terminal.value]

    with pytest.raises(RuntimeError, match="状态转换"):
        probe.update_status(terminal, result={"summary": "must not replace"})
    assert _events(mysql, task_id) == ["pending", "running", terminal.value]


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_agent_tool_delegates_the_only_running_claim_to_pipeline(mysql_task, monkeypatch):
    from app.services.tools.pipeline_run import PipelineRunTool

    mysql, _, student_id, _ = mysql_task
    tool = PipelineRunTool()
    tool._mysql = mysql

    def execute_pipeline(_self, pipeline, params, *, task_id):
        # Mirrors the actual delegated BasePipeline.run() state calls while
        # replacing OCR/model work, which is unrelated to the claim contract.
        probe = _StatusProbe(task_id, params, mysql)
        probe.update_status(PipelineStatus.RUNNING)
        result = {"status": "partial", "summary": "synthetic incomplete L0"}
        probe.update_status(PipelineStatus.PARTIAL, result)
        return result

    monkeypatch.setattr(PipelineRunTool, "_execute_pipeline", execute_pipeline)
    result = json.loads(tool.execute(pipeline="L0_parse_resume", params={"text": "synthetic"},
                                     student_id=student_id))

    assert result["status"] == "partial", result
    rows = mysql.fetch_all("SELECT task_id,status FROM pipeline_task WHERE student_id=:student_id",
                           {"student_id": student_id})
    assert len(rows) == 1 and rows[0]["status"] == "partial"
    assert _events(mysql, rows[0]["task_id"]) == ["pending", "running", "partial"]


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_agent_tool_real_l0_executor_uses_one_task_and_one_claim(mysql_task, monkeypatch):
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser, L0Result
    from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
    from app.services.tools.pipeline_run import PipelineRunTool

    mysql, _, student_id, _ = mysql_task
    tool = PipelineRunTool()
    tool._mysql = mysql
    monkeypatch.setattr(L0FullParser, "parse_text", lambda _self, _text: L0Result(
        success=True, text="synthetic resume text", blocks=[], metadata={}, engine="fixture"))
    monkeypatch.setattr(NSLEPipeline, "run_resume_text", lambda _self, _text: {
        "success": True, "student_profile": {}, "structure_validation": {"ok": False},
    })

    result = json.loads(tool.execute(pipeline="L0_parse_resume", params={"text": "synthetic"},
                                     student_id=student_id))

    assert result["status"] == "partial", result
    rows = mysql.fetch_all("SELECT task_id,status FROM pipeline_task WHERE student_id=:student_id",
                           {"student_id": student_id})
    assert len(rows) == 1 and rows[0]["status"] == "partial"
    assert rows[0]["task_id"] == result["task_id"]
    assert _events(mysql, rows[0]["task_id"]) == ["pending", "running", "partial"]


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_strict_upload_http_claims_and_settles_one_l0_task(mysql_task, monkeypatch, tmp_path):
    from io import BytesIO

    from flask import g

    from app import create_app
    from app.api._helpers import Principal
    from app.config import Config
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser, L0Result
    from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline

    mysql, _, student_id, _ = mysql_task
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(L0FullParser, "parse_file", lambda _self, _path: L0Result(
        success=True, text="synthetic resume text", blocks=[], metadata={}, engine="fixture"))
    monkeypatch.setattr(NSLEPipeline, "run_resume_text", lambda _self, _text: {
        "success": True, "student_profile": {}, "structure_validation": {"ok": False},
    })
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = Principal(student_id, student_id, "live-test")

    response = app.test_client().post(
        "/api/profile/student/upload",
        data={"file": (BytesIO(b"synthetic fixture"), "resume.txt")},
    )

    assert response.status_code == 200, response.json
    data = response.json["data"]
    assert data["status"] == "partial"
    rows = mysql.fetch_all("SELECT task_id,status FROM pipeline_task WHERE student_id=:student_id",
                           {"student_id": student_id})
    assert len(rows) == 1 and rows[0]["status"] == "partial"
    assert rows[0]["task_id"] == data["task_id"]
    assert _events(mysql, data["task_id"]) == ["pending", "running", "partial"]


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_agent_tool_postprocess_error_cannot_erase_committed_terminal(mysql_task, monkeypatch):
    from app.services.tools.pipeline_run import PipelineRunTool

    mysql, _, student_id, _ = mysql_task
    tool = PipelineRunTool()
    tool._mysql = mysql

    def execute_pipeline(_self, pipeline, params, *, task_id):
        probe = _StatusProbe(task_id, params, mysql)
        probe.update_status(PipelineStatus.RUNNING)
        probe.update_status(PipelineStatus.COMPLETED,
                            {"status": "completed", "summary": "persisted before postprocess failure"})
        # The transport result disagrees with the already-committed task.
        return {"success": False, "summary": "synthetic postprocess failure"}

    monkeypatch.setattr(PipelineRunTool, "_execute_pipeline", execute_pipeline)
    result = json.loads(tool.execute(pipeline="L0_parse_resume", params={"text": "synthetic"},
                                     student_id=student_id))

    rows = mysql.fetch_all("SELECT task_id,status FROM pipeline_task WHERE student_id=:student_id",
                           {"student_id": student_id})
    assert len(rows) == 1 and rows[0]["status"] == "completed"
    assert _events(mysql, rows[0]["task_id"]) == ["pending", "running", "completed"]
    assert result["status"] == "unverified"
    assert result["authoritative_task_status"] == "completed"
    assert result["success"] is False
    assert result.get("artifact_candidates") == []


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_agent_tool_setup_error_fails_unclaimed_task_once(mysql_task, monkeypatch):
    from app.services.tools.pipeline_run import PipelineRunTool

    mysql, _, student_id, _ = mysql_task
    tool = PipelineRunTool()
    tool._mysql = mysql

    def fail_before_claim(*args, **kwargs):
        raise RuntimeError("synthetic executor setup failure")

    monkeypatch.setattr(PipelineRunTool, "_execute_pipeline", fail_before_claim)
    result = json.loads(tool.execute(pipeline="L0_parse_resume", params={"text": "synthetic"},
                                     student_id=student_id))

    rows = mysql.fetch_all("SELECT task_id,status FROM pipeline_task WHERE student_id=:student_id",
                           {"student_id": student_id})
    assert len(rows) == 1 and rows[0]["status"] == "failed"
    assert _events(mysql, rows[0]["task_id"]) == ["pending", "failed"]
    assert result["status"] == "failed"


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_agent_tool_wrapper_error_does_not_kill_claimed_runner(mysql_task, monkeypatch):
    from app.services.tools.pipeline_run import PipelineRunTool

    mysql, _, student_id, _ = mysql_task
    tool = PipelineRunTool()
    tool._mysql = mysql

    def execute_pipeline(_self, pipeline, params, *, task_id):
        _StatusProbe(task_id, params, mysql).update_status(PipelineStatus.RUNNING)
        raise RuntimeError("synthetic wrapper-observed interruption")

    monkeypatch.setattr(PipelineRunTool, "_execute_pipeline", execute_pipeline)
    result = json.loads(tool.execute(pipeline="L0_parse_resume", params={"text": "synthetic"},
                                     student_id=student_id))

    rows = mysql.fetch_all("SELECT task_id,status FROM pipeline_task WHERE student_id=:student_id",
                           {"student_id": student_id})
    assert len(rows) == 1 and rows[0]["status"] == "running"
    assert _events(mysql, rows[0]["task_id"]) == ["pending", "running"]
    assert result["status"] == "unverified"
    assert result["authoritative_task_status"] == "running"
