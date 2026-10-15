"""Owner-bound role exploration must use the confirmed resume, not request skills.

Run with CPA_TEST_PIPELINE_MYSQL=1 against the local MySQL service.
"""

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
def test_confirmed_resume_roles_read_owner_bound_skills_not_client_skills(monkeypatch):
    from app.api.profile import profile_bp
    from app.services import career_role_inference
    from app.services.db import get_mysql_client

    mysql = get_mysql_client()
    if not mysql.test_connection():
        pytest.skip("MySQL unavailable")
    owner = "confirmed_roles_" + uuid.uuid4().hex[:12]
    other = "confirmed_roles_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:16]
    candidate = {"school": "示例大学", "major": "计算机科学", "degree": "本科",
                 "skills": ["Python", "TypeScript"]}
    evidence = {"page_count": 1, "observed_pages": [1], "missing_pages": []}
    result = {"student_id": owner, "task_id": task_id, "confirmed": True,
              "profile_ready": True, "candidate_profile": candidate,
              "source_evidence": evidence}
    profile = {**candidate, "_resume_confirmation": {
        "task_id": task_id, "student_id": owner, "source_evidence": evidence}}
    calls = []

    def infer(skills, *, top_k=5, preferred_role=None, **_kwargs):
        calls.append({"skills": skills, "top_k": top_k, "preferred_role": preferred_role})
        return {"status": "partial", "predicted_role": "前端开发", "confidence": 0.5,
                "candidates": [{"role_name": "前端开发", "matched_skills": ["TypeScript"]}],
                "skill_count": len(skills)}

    monkeypatch.setattr(career_role_inference, "infer_roles_from_skills", infer)
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    identity = {"principal_id": owner, "student_id": owner}

    @app.before_request
    def principal():
        g.cpa_principal = identity.copy()

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    session = requests.Session()
    session.trust_env = False
    try:
        mysql.execute("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
                      "VALUES (:tid,:sid,'L0_parse_resume','completed',:result)",
                      {"tid": task_id, "sid": owner,
                       "result": json.dumps(result, ensure_ascii=False)}, commit=True)
        mysql.execute("INSERT INTO student_profile(student_id,profile_json) VALUES (:sid,:profile)",
                      {"sid": owner, "profile": json.dumps(profile, ensure_ascii=False)}, commit=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/profile/student/confirmed-resume/roles"

        forged = session.post(base, json={"task_id": task_id,
                                          "preferred_role": "前端开发",
                                          "skills": ["伪造技能"]}, timeout=15)
        assert forged.status_code == 400, forged.text
        assert calls == []

        roles = session.post(base, json={"task_id": task_id,
                                         "preferred_role": "前端开发"}, timeout=15)
        assert roles.status_code == 200, roles.text
        data = roles.json()["data"]
        assert data["source_confirmation_task_id"] == task_id
        assert data["source_kind"] == "confirmed_resume"
        assert data["scoring_eligible"] is False
        assert data["exploration_only"] is True
        assert data["roles"]["predicted_role"] == "前端开发"
        assert calls == [{"skills": ["Python", "TypeScript"], "top_k": 5,
                          "preferred_role": "前端开发"}]

        stale = session.post(base, json={"task_id": "task_oldversion"}, timeout=15)
        assert stale.status_code == 409
        assert len(calls) == 1
        identity.update({"principal_id": other, "student_id": other})
        denied = session.post(base, json={"task_id": task_id}, timeout=15)
        assert denied.status_code in (404, 409)
        assert len(calls) == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_task_event WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})
        session.close()
