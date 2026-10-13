"""Opt-in MySQL proof of L0 acceptance, actual checkpoints and owner isolation."""

import os
import uuid
from types import SimpleNamespace

import pytest

from app.services.db.mysql_client import get_mysql_client
from app.services.pipeline_resume_jobs import (
    execute_resume_intent,
    install_resume_dispatch,
    persist_resume_intent,
    read_resume_trace,
)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL opt-in")
def test_resume_trace_tracks_persisted_work_not_mock_progress(monkeypatch):
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser
    from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
    from app.services.resume_agent_team import EntityAgent

    mysql = get_mysql_client()
    install_resume_dispatch(mysql)
    owner = "resume_trace_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:12]
    parsed = SimpleNamespace(success=True, text="学校 专业 Python", engine="test_parser",
        metadata={"pages": 2}, blocks=[
            SimpleNamespace(block_id="b1", text="学校 专业 Python", page=0, block_type="text"),
        ])
    monkeypatch.setattr(L0FullParser, "parse_file", lambda self, path: parsed)
    monkeypatch.setattr(NSLEPipeline, "__init__", lambda self, *args, **kwargs: setattr(self, "_llm_client", object()))
    monkeypatch.setattr(NSLEPipeline, "run_resume_text", lambda self, text, *, link_entities=True: {
        "success": True, "student_profile": {"school": "学校", "major": "专业", "skills": ["Python"],
                                               "projects": [{"name": "测试项目"}]},
        "structure_validation": {"ok": True},
    })
    monkeypatch.setattr(EntityAgent, "run", lambda self, profile, llm, document: profile)
    try:
        persist_resume_intent(mysql, student_id=owner, file_path="test-resume.txt", task_id=task_id)
        initial = read_resume_trace(mysql, student_id=owner, task_id=task_id)
        assert initial["task"]["status"] == "pending"
        assert initial["task"]["progress"] == 0
        assert [event["stage"] for event in initial["events"]] == ["file_received"]
        assert initial["progress_basis"] == "4_completed_checkpoints_not_elapsed_time"

        result = execute_resume_intent(task_id, mysql=mysql)
        assert result["status"] == "needs_input"
        replay = read_resume_trace(mysql, student_id=owner, task_id=task_id)
        assert replay["task"]["status"] == "needs_input"
        assert replay["task"]["progress"] == 100
        assert [(event["stage"], event["progress"]) for event in replay["events"]] == [
            ("file_received", 0), ("source_extract", 0), ("source_extracted", 25),
            ("handoff_document", 25), ("structure_extract", 25),
            ("structure_extracted", 50), ("handoff_structure", 50),
            ("entity_extract", 50), ("entity_resolved", 75),
            ("handoff_entity", 75), ("evidence_assemble", 75),
            ("evidence_assembled", 75), ("candidate_saved", 100),
        ]
        assert replay["events"][2]["facts"]["block_count"] == 1
        assert replay["events"][11]["facts"]["missing_page_count"] == 1
        assert [event["source"] + "→" + event["target"] for event in replay["events"] if event["kind"] == "handoff"] == [
            "DocumentAgent→StructureAgent", "StructureAgent→EntityAgent",
            "EntityAgent→EvidenceAgent", "EvidenceAgent→CandidateStore",
        ]
        assert all("学校" not in str(event) for event in replay["events"])
        assert execute_resume_intent(task_id, mysql=mysql)["status"] == "already_claimed_or_terminal"
        assert len(read_resume_trace(mysql, student_id=owner, task_id=task_id)["events"]) == 13
        with pytest.raises(LookupError):
            read_resume_trace(mysql, student_id="other_student", task_id=task_id)
    finally:
        mysql.execute("DELETE FROM pipeline_task_trace WHERE task_id=:tid", {"tid": task_id}, commit=True)
        mysql.execute("DELETE FROM pipeline_resume_dispatch WHERE task_id=:tid", {"tid": task_id}, commit=True)
        mysql.execute("DELETE FROM pipeline_task_event WHERE task_id=:tid", {"tid": task_id}, commit=True)
        mysql.execute("DELETE FROM pipeline_task WHERE task_id=:tid", {"tid": task_id}, commit=True)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL opt-in")
def test_resume_trace_failure_keeps_last_real_checkpoint(monkeypatch):
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser
    from app.services.resume_agent_team import StructureAgent

    mysql = get_mysql_client()
    install_resume_dispatch(mysql)
    owner = "resume_trace_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:12]
    parsed = SimpleNamespace(success=True, text="学校 专业 项目", engine="test_parser",
                             metadata={}, blocks=[])
    monkeypatch.setattr(L0FullParser, "parse_file", lambda self, path: parsed)
    monkeypatch.setattr(StructureAgent, "run", lambda self, document: (_ for _ in ()).throw(RuntimeError("model offline")))
    try:
        persist_resume_intent(mysql, student_id=owner, file_path="test-resume.txt", task_id=task_id)
        assert execute_resume_intent(task_id, mysql=mysql)["status"] == "failed"
        replay = read_resume_trace(mysql, student_id=owner, task_id=task_id)
        assert replay["task"]["status"] == "failed"
        assert replay["task"]["progress"] == 25
        assert replay["events"][-1]["stage"] == "failed"
        assert replay["events"][-1]["progress"] == 25
        assert not any(event["stage"] == "candidate_saved" for event in replay["events"])
    finally:
        mysql.execute("DELETE FROM pipeline_task_trace WHERE task_id=:tid", {"tid": task_id}, commit=True)
        mysql.execute("DELETE FROM pipeline_resume_dispatch WHERE task_id=:tid", {"tid": task_id}, commit=True)
        mysql.execute("DELETE FROM pipeline_task_event WHERE task_id=:tid", {"tid": task_id}, commit=True)
        mysql.execute("DELETE FROM pipeline_task WHERE task_id=:tid", {"tid": task_id}, commit=True)
