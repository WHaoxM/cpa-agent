"""Owner-scoped HTTP replay of self-report → revised portrait candidate."""

import json
import os
import uuid

import pytest
from flask import Flask, g
from sqlalchemy import text


def test_self_report_is_versioned_and_never_becomes_resume_evidence(monkeypatch):
    if os.getenv("CPA_TEST_PIPELINE_MYSQL") != "1":
        pytest.skip("requires explicit MySQL integration opt-in")
    from app.api.profile import profile_bp
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
    from app.services import pipeline, personal_graph_jobs
    from app.services.agent_runtime.pipeline_reference import resolve_pipeline_task_ref
    from app.services.agent_runtime import resume_confirmation
    from app.services.db import get_mysql_client

    mysql = get_mysql_client()
    if not mysql.test_connection():
        pytest.skip("MySQL unavailable")
    owner = "selfreport_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:16]
    candidate = {"name": "Fixture Student", "school": "Fixture University",
                 "major": "Computer Science", "degree": "本科", "skills": ["Python"]}
    result = {"task_id": task_id, "student_id": owner, "status": "needs_input",
              "candidate_profile": candidate, "structure_validation": {"ok": True},
              "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": []}}
    monkeypatch.setattr(resume_confirmation, "run_confirmation", lambda **kwargs:
                        pipeline.confirm_resume_candidate(mysql, student_id=kwargs["student_id"],
                                                          task_id=kwargs["task_id"]))
    monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                        lambda *args, **kwargs: {"status": "not_requested"})
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    identity = {"principal_id": owner, "student_id": owner}

    @app.before_request
    def principal():
        g.cpa_principal = identity

    client = app.test_client()
    items = {key: {"level": "中", "example": f"我在一次团队项目中负责{key}，完成沟通协调并按期交付。"}
             for key in ("communication", "teamwork", "stress_tolerance", "responsibility")}
    try:
        mysql.execute("INSERT INTO pipeline_task(task_id, student_id, pipeline, status, result_json) "
                      "VALUES (:tid,:sid,'L0_parse_resume','needs_input',:result)",
                      {"tid": task_id, "sid": owner, "result": json.dumps(result)}, commit=True)
        assert client.post("/api/profile/student/confirm", json={
            "task_id": task_id, "confirmed": True}).status_code == 200
        initial = client.post("/api/profile/student/pipeline", json={"pipeline": "L2_generate_portrait"})
        assert initial.status_code == 200, initial.json
        assert initial.json["data"]["status"] == "partial"
        assert "communication" in initial.json["data"]["missing_fields"]

        saved = client.post("/api/profile/student/self-report", json={
            "task_id": task_id, "expected_revision": 0, "items": items})
        assert saved.status_code == 200, saved.json
        assert saved.json["data"]["revision"] == 1
        assert saved.json["data"]["source_kind"] == "user_self_report"
        assert client.post("/api/profile/student/self-report", json={
            "task_id": task_id, "expected_revision": 0, "items": items}).status_code == 409
        replay = client.get(f"/api/profile/student/candidate/{task_id}")
        assert replay.status_code == 200, replay.json
        assert replay.json["data"]["user_self_report"]["revision"] == 1
        assert replay.json["data"]["portrait_task"] is None  # old L2 invalidated
        stored = pipeline.require_confirmed_profile(mysql, owner)
        assert stored["_user_self_report"]["items"] == items
        assert "semantic_facts" not in stored
        assert "_user_self_report" not in result["candidate_profile"]

        regenerated = client.post("/api/profile/student/pipeline", json={"pipeline": "L2_generate_portrait"})
        assert regenerated.status_code == 200, regenerated.json
        portrait = regenerated.json["data"]["portrait_candidate"]
        assert portrait["source_self_report_revision"] == 1
        assert portrait["D3_quality"]["dimension_score"] == 48
        assert portrait["D3_quality"]["communication"]["provenance"] == {
            "kind": "user_self_report", "revision": 1}
        assert portrait["D3_quality"]["communication"]["evidence"] == items["communication"]["example"]
        assert portrait["status"] == "partial"  # school/major proof still absent
        assert mysql.execute("SELECT id FROM student_portrait_4d WHERE student_id=:sid",
                             {"sid": owner}) == []
        withdrawn = client.post("/api/profile/student/self-report", json={
            "task_id": task_id, "expected_revision": 1,
            "items": {key: None for key in items}})
        assert withdrawn.status_code == 200, withdrawn.json
        assert withdrawn.json["data"]["revision"] == 2
        assert client.get(f"/api/profile/student/candidate/{task_id}").json["data"]["portrait_task"] is None
        reverted = client.post("/api/profile/student/pipeline", json={"pipeline": "L2_generate_portrait"})
        assert reverted.status_code == 200, reverted.json
        assert reverted.json["data"]["portrait_candidate"]["D3_quality"]["dimension_score"] is None
        monkeypatch.setattr(PortraitGenerator, "generate_student_portrait", lambda self, **kwargs: {
            "status": "completed", "missing_fields": [],
            "D1_basic": {"dimension_score": 60},
            "D2_skill": {"dimension_score": 65, "skill_coverage": 0.65},
            "D3_quality": {"dimension_score": 62}, "D4_potential": {"dimension_score": 65},
            "summary": {"competitiveness_score": 63, "competitiveness_level": "中",
                        "completeness_score": 100},
        })
        completed = client.post("/api/profile/student/pipeline", json={"pipeline": "L2_generate_portrait"})
        assert completed.status_code == 200, completed.json
        assert completed.json["data"]["status"] == "completed"
        completed_task_id = completed.json["data"]["task_id"]
        assert client.get(f"/api/profile/student/{owner}/4d").status_code == 200
        updated = client.post("/api/profile/student/self-report", json={
            "task_id": task_id, "expected_revision": 2, "items": items})
        assert updated.status_code == 200, updated.json
        assert client.get(f"/api/profile/student/{owner}/4d").status_code == 409
        assert client.get(f"/api/profile/student/candidate/{task_id}").json["data"]["portrait_task"] is None
        with pytest.raises(ValueError, match="用户自述已更新"):
            resolve_pipeline_task_ref(mysql, student_id=owner, task_id=completed_task_id)
        identity.update({"principal_id": "other", "student_id": "other"})
        forbidden = client.post("/api/profile/student/self-report", json={
            "task_id": task_id, "expected_revision": 3, "items": items})
        assert forbidden.status_code in (404, 409)
    finally:
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_task_event WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_portrait_4d WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})
