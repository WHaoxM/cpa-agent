"""Real HTTP proof for registered-account L0 async upload and owner-scoped trace."""

import os
import threading
import uuid
import json
from pathlib import Path

import pytest
import requests
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires Docker MySQL and Redis",
)


def test_registered_student_uploads_async_and_reads_owned_trace(monkeypatch, tmp_path):
    from app import create_app
    from app.config import Config
    from app.services.db.mysql_client import get_mysql_client
    from app.services import pipeline_resume_jobs as jobs
    from sqlalchemy import text

    nonce = uuid.uuid4().hex[:12]
    username = f"trace_student_{nonce}"
    other_name = f"trace_other_{nonce}"
    password = "a-strong-trace-test-password-42"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    config = type("ResumeTraceHttpConfig", (Config,), {
        "SECRET_KEY": "only-for-live-test-a-long-randomly-shaped-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "UPLOAD_FOLDER": str(tmp_path),
        "RATELIMIT_KEY_PREFIX": f"resume-trace-test-{nonce}",
    })
    # Use the real Redis broker, but isolate the message from production workers
    # so this test never invokes OCR or a model.
    isolated_queue = f"trace-audit-{nonce}"
    monkeypatch.setattr(jobs, "PIPELINE_QUEUE", isolated_queue)
    app = create_app(config)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    student, other = requests.Session(), requests.Session()
    db = get_mysql_client()
    task_id = saved_path = None
    try:
        csrf = student.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        credentials = {"username": username, "password": password}
        registered = student.post(f"{base}/api/auth/register", json=credentials,
                                  headers={"X-CSRFToken": csrf}, timeout=5)
        assert registered.status_code == 201, registered.text
        account_id = registered.json()["user"]["id"]
        assert account_id.startswith("acct_")
        login = student.post(f"{base}/api/auth/login", json=credentials,
                             headers={"X-CSRFToken": csrf}, timeout=5)
        assert login.status_code == 200, login.text
        assert student.get(f"{base}/api/auth/me", timeout=5).json()["user"]["id"] == account_id

        uploaded = student.post(
            f"{base}/api/profile/student/upload?async=1",
            files={"file": ("trace-fixture.txt", b"Resume trace integration fixture.\n", "text/plain")},
            headers={"X-CSRFToken": csrf},
            timeout=10,
        )
        assert uploaded.status_code == 202, uploaded.text
        body = uploaded.json()["data"]
        task_id = body["task_id"]
        assert body["student_id"] == account_id
        assert (body["status"], body["progress"]) == ("pending", 0)
        task = db.fetch_one(
            "SELECT pipeline,status,progress,params_json,student_id FROM pipeline_task WHERE task_id=:tid",
            {"tid": task_id},
        )
        assert task and task["pipeline"] == "L0_parse_resume"
        assert task["student_id"] == account_id
        assert (task["status"], task["progress"]) == ("pending", 0)
        params = json.loads(task["params_json"])
        saved_path = params["file_path"]
        assert os.path.isfile(saved_path)
        assert Path(saved_path).resolve().is_relative_to(tmp_path.resolve())

        trace = student.get(f"{base}/api/pipeline/trace/{task_id}", timeout=5)
        assert trace.status_code == 200, trace.text
        payload = trace.json()["data"]
        assert payload["task"]["status"] == "pending"
        assert payload["task"]["progress"] == 0
        assert payload["task"]["current_attempt"] == 0
        assert [event["stage"] for event in payload["events"]] == ["file_received"]
        assert payload["events"][0]["attempt"] is None
        assert payload["progress_basis"] == "4_completed_checkpoints_not_elapsed_time"

        # A leased worker can crash after one real checkpoint. A new claim
        # must keep the old audit trail without presenting it as current work.
        first = jobs.claim_resume_intent(db, task_id)
        assert first and first["attempt"] == 1
        db.execute("UPDATE pipeline_task SET status='running' WHERE task_id=:tid",
                   {"tid": task_id}, commit=True)
        jobs.record_resume_stage(db, task_id=task_id, student_id=account_id,
                                 claim_token=first["claim_token"], stage="source_extracted",
                                 state="completed", progress=25)
        first_http = student.get(f"{base}/api/pipeline/trace/{task_id}", timeout=5)
        assert first_http.status_code == 200, first_http.text
        assert first_http.json()["data"]["task"]["progress"] == 25
        db.execute("UPDATE pipeline_resume_dispatch SET lease_until=NOW(6)-INTERVAL 1 SECOND "
                   "WHERE task_id=:tid", {"tid": task_id}, commit=True)
        second = jobs.claim_resume_intent(db, task_id)
        assert second and second["attempt"] == 2
        retry_http = student.get(f"{base}/api/pipeline/trace/{task_id}?limit=2", timeout=5)
        assert retry_http.status_code == 200, retry_http.text
        first_page = retry_http.json()["data"]
        assert (first_page["task"]["current_attempt"], first_page["task"]["progress"]) == (2, 0)
        assert first_page["has_more"] is True
        assert [(e["stage"], e["attempt"]) for e in first_page["events"]] == [
            ("file_received", None), ("attempt_started", 1)]
        retry_tail = student.get(
            f"{base}/api/pipeline/trace/{task_id}",
            params={"cursor": first_page["cursor"], "limit": 2}, timeout=5)
        assert retry_tail.status_code == 200, retry_tail.text
        assert [(e["stage"], e["attempt"]) for e in retry_tail.json()["data"]["events"]] == [
            ("source_extracted", 1), ("attempt_started", 2)]
        assert retry_tail.json()["data"]["task"]["progress"] == 0

        other_csrf = other.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        other_credentials = {"username": other_name, "password": password}
        other_registered = other.post(f"{base}/api/auth/register", json=other_credentials,
                                      headers={"X-CSRFToken": other_csrf}, timeout=5)
        assert other_registered.status_code == 201, other_registered.text
        assert other.post(f"{base}/api/auth/login", json=other_credentials,
                          headers={"X-CSRFToken": other_csrf}, timeout=5).status_code == 200
        denied = other.get(f"{base}/api/pipeline/trace/{task_id}", timeout=5)
        assert denied.status_code == 404, denied.text
    finally:
        student.close()
        other.close()
        server.shutdown()
        thread.join(timeout=5)
        if task_id:
            with db.engine.begin() as conn:
                for table in ("pipeline_task_trace", "pipeline_resume_dispatch",
                              "pipeline_task_event", "pipeline_task"):
                    conn.execute(text(f"DELETE FROM {table} WHERE task_id=:tid"), {"tid": task_id})
        db.execute_write("DELETE FROM user_account WHERE username IN (:u1,:u2)",
                          {"u1": username, "u2": other_name})
        # Remove only this test's uniquely named Redis queue/message.
        from app.services.agent_runtime.media.jobs import celery_app
        from redis import Redis
        with celery_app.connection_for_write() as broker:
            broker.default_channel.queue_delete(isolated_queue)
        # Kombu's queue_delete may leave the Redis list itself behind.
        Redis.from_url(celery_app.conf.broker_url).delete(isolated_queue)
        if saved_path and os.path.isfile(saved_path):
            os.remove(saved_path)
            try:
                os.rmdir(os.path.dirname(saved_path))
            except OSError:
                pass
