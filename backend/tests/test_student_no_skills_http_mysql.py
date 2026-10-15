"""A confirmed resume with no extracted skills is incomplete, not a crashed L2."""

import json
import os
import threading
import uuid

from flask import Flask, g
import pytest
import requests
from sqlalchemy import text
from werkzeug.serving import make_server


@pytest.mark.skipif(os.getenv("CPA_TEST_PIPELINE_MYSQL") != "1", reason="requires live MySQL opt-in")
def test_confirmed_resume_without_skill_evidence_returns_partial_over_http(monkeypatch):
    from app.api.profile import profile_bp
    from app.services import pipeline, personal_graph_jobs
    from app.services.agent_runtime import resume_confirmation
    from app.services.db import get_mysql_client

    mysql = get_mysql_client()
    if not mysql.test_connection():
        pytest.skip("MySQL unavailable")
    owner = "no_skills_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:16]
    candidate = {"name": "无技能证据夹具", "school": "示例大学", "major": "计算机科学",
                 "degree": "本科", "skills": [],
                 "projects": [{"name": "课程组织项目", "role": "成员", "desc": "负责会议安排"}]}
    result = {"task_id": task_id, "student_id": owner, "status": "needs_input",
              "candidate_profile": candidate, "structure_validation": {"ok": True},
              "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": [],
                                  "blocks": [{"block_id": "b1", "page": 1,
                                              "block_type": "text", "text": "课程组织项目"}]}}
    monkeypatch.setattr(resume_confirmation, "run_confirmation", lambda **kwargs:
                        pipeline.confirm_resume_candidate(mysql, student_id=kwargs["student_id"],
                                                          task_id=kwargs["task_id"]))
    monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                        lambda *_args, **_kwargs: {"status": "not_requested"})
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")

    @app.before_request
    def principal():
        g.cpa_principal = {"principal_id": owner, "student_id": owner}

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        mysql.execute("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
                      "VALUES (:tid,:sid,'L0_parse_resume','needs_input',:result)",
                      {"tid": task_id, "sid": owner, "result": json.dumps(result, ensure_ascii=False)},
                      commit=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        confirmed = requests.post(f"{base}/api/profile/student/confirm",
                                  json={"task_id": task_id, "confirmed": True}, timeout=20)
        assert confirmed.status_code == 200, confirmed.text
        response = requests.post(f"{base}/api/profile/student/pipeline",
                                 json={"pipeline": "L2_generate_portrait"}, timeout=30)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["status"] == "partial"
        assert "skills" in data["missing_fields"]
        assert data["next_action"] == "reupload_resume"
        assert data["resume_state"] == "new_upload_required"
        assert data["action_available"] is False
        assert data["portrait_candidate"]["D2_skill"]["dimension_score"] is None
        assert data["portrait_candidate"]["D2_skill"]["project_count"] == 1
        assert data["portrait_candidate"]["summary"]["competitiveness_score"] is None
        stored = mysql.fetch_one("SELECT status,result_json FROM pipeline_task WHERE task_id=:tid",
                                 {"tid": data["task_id"]})
        assert stored["status"] == "partial"
        replay = requests.get(f"{base}/api/profile/student/candidate/{task_id}", timeout=20)
        assert replay.status_code == 200, replay.text
        assert replay.json()["data"]["portrait_task"]["task_id"] == data["task_id"]
        assert replay.json()["data"]["portrait_task"]["next_action"] == "reupload_resume"
        assert mysql.fetch_one("SELECT id FROM student_portrait_4d WHERE student_id=:sid",
                               {"sid": owner}) is None
    finally:
        server.shutdown()
        if thread.is_alive():
            thread.join(timeout=5)
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_task_event WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_portrait_4d WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})
