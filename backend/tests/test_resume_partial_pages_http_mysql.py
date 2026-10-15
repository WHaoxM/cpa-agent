"""Real MySQL + HTTP route proof for acknowledging only readable resume pages."""

import json
import os
import uuid

import pytest
from flask import Flask, g
from sqlalchemy import text


def test_partial_page_confirmation_keeps_l2_candidate_unpublished(monkeypatch):
    if os.getenv("CPA_TEST_PIPELINE_MYSQL") != "1":
        pytest.skip("requires explicit MySQL integration opt-in")

    from app.api.profile import profile_bp
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
    from app.services import pipeline
    from app.services.agent_runtime import resume_confirmation
    from app.services import personal_graph_jobs
    from app.services.db import get_mysql_client

    mysql = get_mysql_client()
    if not mysql.test_connection():
        pytest.skip("MySQL unavailable")
    owner = "partial_" + uuid.uuid4().hex[:16]
    task_id = "task_" + uuid.uuid4().hex[:16]
    candidate = {"name": "Fixture Student", "school": "Fixture University",
                 "major": "Computer Science", "skills": ["Python"]}
    source = {"page_count": 2, "observed_pages": [1], "missing_pages": [2],
              "blocks": [{"block_id": "block_1", "page": 1, "block_type": "text",
                          "text": "Fixture University Computer Science Python"}]}
    result = {"task_id": task_id, "student_id": owner, "status": "needs_input",
              "candidate_profile": candidate, "structure_validation": {"ok": True},
              "source_evidence": source}
    monkeypatch.setattr(resume_confirmation, "run_confirmation", lambda **kwargs:
                        pipeline.confirm_resume_candidate(mysql, student_id=kwargs["student_id"],
                                                          task_id=kwargs["task_id"],
                                                          acknowledge_partial=kwargs["acknowledge_partial"]))
    monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                        lambda *args, **kwargs: {"status": "not_requested"})
    monkeypatch.setattr(PortraitGenerator, "generate_student_portrait", lambda self, **kwargs: {
        "status": "completed", "D1_basic": {"dimension_score": 60},
        "D2_skill": {"dimension_score": 65, "skill_coverage": 0.65},
        "D3_quality": {"dimension_score": 62}, "D4_potential": {"dimension_score": 65},
        "summary": {"competitiveness_score": 63},
    })
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")

    @app.before_request
    def principal():
        g.cpa_principal = {"principal_id": owner, "student_id": owner}

    client = app.test_client()
    try:
        mysql.execute("INSERT INTO pipeline_task(task_id, student_id, pipeline, status, result_json) "
                      "VALUES (:tid,:sid,'L0_parse_resume','needs_input',:result)",
                      {"tid": task_id, "sid": owner, "result": json.dumps(result)}, commit=True)
        blocked = client.post("/api/profile/student/confirm",
                              json={"task_id": task_id, "confirmed": True})
        assert blocked.status_code == 409
        assert mysql.execute("SELECT student_id FROM student_profile WHERE student_id=:sid",
                             {"sid": owner}) == []
        confirmed = client.post("/api/profile/student/confirm", json={
            "task_id": task_id, "confirmed": True, "acknowledge_partial": True})
        assert confirmed.status_code == 200, confirmed.json
        assert confirmed.json["data"]["profile_ready"] is True
        generated = client.post("/api/profile/student/pipeline",
                                json={"pipeline": "L2_generate_portrait"})
        assert generated.status_code == 200, generated.json
        assert generated.json["data"]["status"] == "partial"
        assert generated.json["data"]["portrait_id"] is None
        assert generated.json["data"]["portrait_candidate"]["summary"]["competitiveness_score"] is None
        assert "source_pages" in generated.json["data"]["missing_fields"]
        stored = mysql.execute("SELECT status,result_json FROM pipeline_task WHERE task_id=:tid",
                               {"tid": generated.json["data"]["task_id"]})[0]
        assert stored["status"] == "partial"
        assert (stored["result_json"] if isinstance(stored["result_json"], dict)
                else json.loads(stored["result_json"]))["next_action"] == "reupload_resume"
        assert mysql.execute("SELECT id FROM student_portrait_4d WHERE student_id=:sid",
                             {"sid": owner}) == []
        replay = client.get(f"/api/profile/student/candidate/{task_id}")
        assert replay.status_code == 200
        assert replay.json["data"]["portrait_task"]["resume_state"] == "new_upload_required"
    finally:
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_task_event WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_portrait_4d WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})
