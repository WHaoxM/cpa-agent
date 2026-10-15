"""An L0 async upload without a durable task must not retain an orphaned file."""

from io import BytesIO
import errno
import os
import threading

import pytest
import requests
from werkzeug.serving import make_server

from app import create_app
from app.api import profile as profile_api
from app import upload_request
from app.config import Config
from app.services import pipeline_resume_jobs


def _post_resume_over_http(app, content=b"fictional resume"):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return requests.post(
            f"http://127.0.0.1:{server.server_port}/api/profile/student/upload?async=1",
            files={"file": ("resume.txt", BytesIO(content), "text/plain")},
            timeout=10,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_async_upload_intent_failure_removes_only_its_untracked_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()
    class MissingIntentDb:
        def fetch_one(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(profile_api, "get_mysql_client", lambda: MissingIntentDb())

    def reject_intent(*args, **kwargs):
        raise RuntimeError("simulated MySQL write failure")

    monkeypatch.setattr(pipeline_resume_jobs, "persist_resume_intent", reject_intent)
    response = _post_resume_over_http(app)

    assert response.status_code == 503
    assert response.json()["success"] is False
    assert list(tmp_path.rglob("resume_*.txt")) == []


def test_resume_multipart_spool_full_returns_explicit_failure_before_task(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()

    def no_spool(**_kwargs):
        raise OSError(errno.ENOSPC, "simulated upload volume full")

    monkeypatch.setattr(upload_request, "SpooledTemporaryFile", no_spool)
    response = _post_resume_over_http(app, b"x" * 600_000)

    assert response.status_code == 507
    assert response.json()["code"] == "upload_storage_full"
    assert "task_id" not in response.json()
    assert list(tmp_path.rglob("resume_*.txt")) == []


def test_resume_save_full_removes_partial_file_without_task(monkeypatch, tmp_path):
    from pathlib import Path
    from werkzeug.datastructures import FileStorage

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()

    def partial_save(_self, destination, **_kwargs):
        Path(destination).write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "simulated durable upload full")

    monkeypatch.setattr(FileStorage, "save", partial_save)
    response = _post_resume_over_http(app)

    assert response.status_code == 507
    assert response.json()["code"] == "upload_storage_full"
    assert "task_id" not in response.json()
    assert list(tmp_path.rglob("resume_*.txt")) == []


def test_resume_multipart_spool_uses_upload_volume_not_system_temp(monkeypatch, tmp_path):
    from tempfile import SpooledTemporaryFile

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()
    seen = []

    def controlled_spool(**kwargs):
        seen.append(kwargs)
        return SpooledTemporaryFile(**kwargs)

    monkeypatch.setattr(upload_request, "SpooledTemporaryFile", controlled_spool)
    class MissingIntentDb:
        def fetch_one(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(profile_api, "get_mysql_client", lambda: MissingIntentDb())
    monkeypatch.setattr(pipeline_resume_jobs, "persist_resume_intent", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("simulated unavailable durable intent")))
    response = _post_resume_over_http(app, b"x" * 600_000)

    assert response.status_code == 503
    assert seen and all(item["dir"] == str(tmp_path) for item in seen)
    assert list(tmp_path.rglob("resume_*.txt")) == []


def test_async_upload_publish_failure_keeps_durable_intent_and_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()
    recorded = {}
    monkeypatch.setattr(profile_api, "get_mysql_client", lambda: object())
    monkeypatch.setattr(
        pipeline_resume_jobs, "persist_resume_intent",
        lambda _mysql, **kwargs: recorded.update(kwargs),
    )

    def reject_publish(*args, **kwargs):
        raise RuntimeError("simulated Redis publish failure")

    monkeypatch.setattr(pipeline_resume_jobs, "enqueue_resume_intent", reject_publish)
    response = _post_resume_over_http(app)

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["task_id"] == recorded["task_id"]
    assert data["delivery"] == "retry_pending"
    assert recorded["file_path"] in [str(path) for path in tmp_path.rglob("resume_*.txt")]


def test_async_upload_unknown_commit_keeps_file_and_returns_lookup_id(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()
    monkeypatch.setattr(profile_api, "get_mysql_client", lambda: (_ for _ in ()).throw(
        RuntimeError("simulated MySQL connection outage")))

    response = _post_resume_over_http(app)

    assert response.status_code == 503
    assert response.json()["data"]["task_id"].startswith("task_")
    assert response.json()["data"]["delivery"] == "verify_pending"
    assert len(list(tmp_path.rglob("resume_*.txt"))) == 1


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL")
def test_async_upload_committed_then_error_keeps_recoverable_task(monkeypatch, tmp_path):
    from sqlalchemy import text
    from app.services.db.mysql_client import get_mysql_client

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()
    db = get_mysql_client()
    persisted = pipeline_resume_jobs.persist_resume_intent
    task_id = None

    def committed_then_error(mysql, **kwargs):
        nonlocal task_id
        task_id = kwargs["task_id"]
        persisted(mysql, **kwargs)
        # Keep this synthetic task out of the live Beat recovery scan.
        mysql.execute(
            "UPDATE pipeline_resume_dispatch SET last_enqueued_at=NOW(6)+INTERVAL 1 DAY "
            "WHERE task_id=:tid", {"tid": task_id}, commit=True,
        )
        raise RuntimeError("simulated lost COMMIT acknowledgement")

    monkeypatch.setattr(pipeline_resume_jobs, "persist_resume_intent", committed_then_error)
    try:
        response = _post_resume_over_http(app)
        assert response.status_code == 202, response.text
        data = response.json()["data"]
        assert data["task_id"] == task_id
        assert data["delivery"] == "retry_pending"
        row = db.fetch_one(
            "SELECT t.status,t.progress,d.task_id AS dispatch_task_id FROM pipeline_task t "
            "JOIN pipeline_resume_dispatch d ON d.task_id=t.task_id WHERE t.task_id=:tid",
            {"tid": task_id},
        )
        assert row == {"status": "pending", "progress": 0, "dispatch_task_id": task_id}
        assert len(list(tmp_path.rglob("resume_*.txt"))) == 1
    finally:
        if task_id:
            with db.engine.begin() as conn:
                for table in ("pipeline_task_trace", "pipeline_resume_dispatch",
                              "pipeline_task_event", "pipeline_task"):
                    conn.execute(text(f"DELETE FROM {table} WHERE task_id=:tid"), {"tid": task_id})
