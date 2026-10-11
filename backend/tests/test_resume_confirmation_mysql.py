"""Production confirmation workflow across fresh Python processes and real MySQL."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import pytest


def child(phase, owner, task):
    from app.services.agent_runtime.resume_confirmation import run_confirmation
    if phase == "domain_commit":
        # Reproduce the crash window: domain commit exists, graph still interrupted.
        from app.services.pipeline import confirm_resume_candidate
        from app.services.db.mysql_client import get_mysql_client
        return confirm_resume_candidate(get_mysql_client(), student_id=owner, task_id=task)
    if phase == "resume":
        from flask import Flask, g
        from app.api.profile import profile_bp
        app = Flask(__name__)
        app.register_blueprint(profile_bp, url_prefix="/api/profile")
        @app.before_request
        def principal():
            g.cpa_principal = {"principal_id": owner, "student_id": owner}
        response = app.test_client().post("/api/profile/student/confirm",
            json={"task_id": task, "confirmed": True})
        if response.status_code == 404:
            return {"error": "not_found"}
        assert response.status_code == 200, response.json
        return response.json["data"]
    try:
        return run_confirmation(student_id=owner, task_id=task, confirmed=phase == "resume")
    except LookupError:
        return {"error": "not_found"}


@pytest.mark.parametrize("crash_after_domain_commit", [False, True])
def test_production_confirmation_restart_owner_and_duplicate(crash_after_domain_commit):
    if os.getenv("CPA_TEST_PIPELINE_MYSQL") != "1":
        pytest.skip("requires explicit MySQL integration opt-in")
    from app.services.db.mysql_client import get_mysql_client
    from app.services.agent_runtime.resume_confirmation import confirmation_thread
    from app.services.pipeline import require_confirmed_profile
    from sqlalchemy import text
    db = get_mysql_client()
    owner, task = "confirm_" + uuid.uuid4().hex[:16], "task_" + uuid.uuid4().hex[:16]
    candidate = {"school": "integration fixture", "skills": ["Python"]}
    result = {"candidate_profile": candidate, "structure_validation": {"ok": True},
              "source_evidence": {"missing_pages": []}}
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1]), "PYTHONUTF8": "1"}
    def run(phase, sid=owner):
        p = subprocess.run([sys.executable, "-B", __file__, phase, sid, task],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout.strip().splitlines()[-1])
    try:
        db.execute("INSERT INTO pipeline_task(task_id, student_id, pipeline, status, result_json) "
            "VALUES (:tid,:sid,'L0_parse_resume','needs_input',:result)",
            {"tid": task, "sid": owner, "result": json.dumps(result)}, commit=True)
        assert run("start")["resume_state"] == "available"
        assert run("resume", "other_owner") == {"error": "not_found"}
        assert run("start")["resume_state"] == "available"
        if crash_after_domain_commit:
            assert run("domain_commit")["profile_ready"] is True
            before_resume = require_confirmed_profile(db, owner)
        assert run("resume")["profile_ready"] is True
        first = require_confirmed_profile(db, owner)
        if crash_after_domain_commit:
            assert first == before_resume
        assert first["school"] == candidate["school"]
        assert run("resume")["profile_ready"] is True
        assert require_confirmed_profile(db, owner) == first
        assert run("start")["resume_state"] == "completed"
    finally:
        with db.engine.begin() as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(text(f"DELETE FROM {table} WHERE thread_id=:tid"),
                             {"tid": confirmation_thread(owner, task)})
            conn.execute(text("DELETE FROM pipeline_task_event WHERE task_id=:tid"), {"tid": task})
            conn.execute(text("DELETE FROM pipeline_task WHERE task_id=:tid"), {"tid": task})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})


if __name__ == "__main__":
    print(json.dumps(child(*sys.argv[1:]), ensure_ascii=False))
