"""Opt-in real socket/MySQL journey for a public Word 97 OLE2 test file.

Only structure/entity model calls are substituted; the binary DOC, Tika/Java,
route, account, durable task, and trace are real.
"""

import json
import os
from pathlib import Path
import threading
import uuid

import pytest
import requests
from sqlalchemy import text
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.getenv("CPA_TEST_REAL_LEGACY_DOC_HTTP") != "1"
    or os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires pinned Tika, public OLE2 sample, Docker MySQL/Redis",
)


def test_registered_doc_upload_tika_candidate_and_unavailable_state(monkeypatch, tmp_path):
    from app import create_app
    from app.config import Config
    from app.services import pipeline_resume_jobs as jobs
    from app.services.db.mysql_client import get_mysql_client
    from app.services.resume_agent_team import EntityAgent, StructureAgent
    from app.utils.legacy_doc_text import OLE2_MAGIC

    sample = Path(os.environ["CPA_TEST_LEGACY_DOC_SAMPLE"])
    assert sample.read_bytes().startswith(OLE2_MAGIC)
    assert Path(os.environ["CPA_L0_DOC_TIKA_JAR"]).is_file()
    nonce = uuid.uuid4().hex[:12]
    username = f"doc_student_{nonce}"
    password = "strong-doc-test-password-42"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    config = type("LegacyDocHttpConfig", (Config,), {
        "SECRET_KEY": "only-for-doc-test-a-long-randomly-shaped-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "UPLOAD_FOLDER": str(tmp_path),
        "RATELIMIT_KEY_PREFIX": f"resume-doc-test-{nonce}",
    })
    isolated_queue = f"doc-audit-{nonce}"
    monkeypatch.setattr(jobs, "PIPELINE_QUEUE", isolated_queue)
    app = create_app(config)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    session = requests.Session()
    db = get_mysql_client()
    task_ids = []
    saved_paths = []
    try:
        csrf = session.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        credentials = {"username": username, "password": password}
        registered = session.post(f"{base}/api/auth/register", json=credentials,
                                  headers={"X-CSRFToken": csrf}, timeout=5)
        assert registered.status_code == 201, registered.text
        student_id = registered.json()["user"]["id"]
        assert session.post(f"{base}/api/auth/login", json=credentials,
                            headers={"X-CSRFToken": csrf}, timeout=5).status_code == 200
        original_profile = db.fetch_one(
            "SELECT profile_json FROM student_profile WHERE student_id=:sid", {"sid": student_id})

        def upload():
            response = session.post(
                f"{base}/api/profile/student/upload?async=1",
                files={"file": ("public-word97.doc", sample.read_bytes(), "application/msword")},
                headers={"X-CSRFToken": csrf}, timeout=10,
            )
            assert response.status_code == 202, response.text
            task_id = response.json()["data"]["task_id"]
            task_ids.append(task_id)
            task = db.fetch_one("SELECT params_json FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
            saved_paths.append(json.loads(task["params_json"])["file_path"])
            return task_id

        profile = {"school": "Synthetic University", "major": "Computer Science",
                   "skills": ["Python"], "projects": [{"name": "Test"}]}
        monkeypatch.setattr(StructureAgent, "run", lambda self, document: (
            {"success": True, "student_profile": profile, "structure_validation": {"ok": True}}, object()))
        monkeypatch.setattr(EntityAgent, "run", lambda self, candidate, llm, document: candidate)

        success_task = upload()
        result = jobs.execute_resume_intent(success_task, mysql=db)
        assert result["status"] == "needs_input"
        assert result["source_evidence"]["engine"] == "apache-tika-doc-text"
        assert result["source_evidence"]["page_count"] is None
        assert result["source_evidence"]["observed_pages"] == []
        assert result["source_evidence"]["blocks"][0]["page"] is None
        assert result["source_evidence"]["coverage"] == "unverified"
        trace_response = session.get(f"{base}/api/pipeline/trace/{success_task}", timeout=5)
        assert trace_response.status_code == 200, trace_response.text
        trace = trace_response.json()["data"]
        assert trace["task"]["progress"] == 100
        source = next(event for event in trace["events"] if event["stage"] == "source_extracted")
        assert source["facts"]["engine"] == "apache-tika-doc-text"
        assert source["facts"]["source_coverage"] == "unverified"
        assert "embedded_media" in source["facts"]["coverage_reason"]
        candidate_response = session.get(
            f"{base}/api/profile/student/candidate/{success_task}", timeout=5)
        assert candidate_response.status_code == 200, candidate_response.text
        candidate = candidate_response.json()["data"]
        assert candidate["candidate_profile"]["school"] == "Synthetic University"
        assert candidate["source_evidence"]["observed_pages"] == []
        assert candidate["source_evidence"]["coverage"] == "unverified"
        assert db.fetch_one("SELECT profile_json FROM student_profile WHERE student_id=:sid",
                            {"sid": student_id}) == original_profile

        # Remove only this test's capability setting, not the host service.
        monkeypatch.delenv("CPA_L0_DOC_TIKA_JAR")
        failed_task = upload()
        assert jobs.execute_resume_intent(failed_task, mysql=db)["status"] == "failed"
        failed = session.get(f"{base}/api/pipeline/trace/{failed_task}", timeout=5).json()["data"]
        assert (failed["task"]["status"], failed["task"]["progress"]) == ("failed", 0)
        assert not any(event["stage"] == "source_extracted" for event in failed["events"])
        status = session.get(f"{base}/api/pipeline/status/{failed_task}", timeout=5).json()["data"]
        assert "CPA_L0_DOC_TIKA_JAR" in status["error"]
        assert session.get(f"{base}/api/profile/student/candidate/{failed_task}", timeout=5).status_code == 409
        assert db.fetch_one("SELECT profile_json FROM student_profile WHERE student_id=:sid",
                            {"sid": student_id}) == original_profile
    finally:
        session.close()
        server.shutdown()
        thread.join(timeout=5)
        with db.engine.begin() as conn:
            for task_id in task_ids:
                for table in ("pipeline_task_trace", "pipeline_resume_dispatch",
                              "pipeline_task_event", "pipeline_task"):
                    conn.execute(text(f"DELETE FROM {table} WHERE task_id=:tid"), {"tid": task_id})
        db.execute_write("DELETE FROM user_account WHERE username=:username", {"username": username})
        from app.services.agent_runtime.media.jobs import celery_app
        from redis import Redis

        with celery_app.connection_for_write() as broker:
            broker.default_channel.queue_delete(isolated_queue)
        Redis.from_url(celery_app.conf.broker_url).delete(isolated_queue)
        for path in saved_paths:
            if os.path.isfile(path):
                os.remove(path)
            try:
                os.rmdir(os.path.dirname(path))
            except OSError:
                pass
