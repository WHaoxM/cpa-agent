"""Real admin HTTP + MySQL + private filesystem source-staging acceptance."""

import hashlib
import io
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from flask import g
from werkzeug.serving import make_server

from app import create_app
from app.api._helpers import Principal
from app.config import Config
from app.services.db import get_mysql_client


@pytest.fixture
def admin_client(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    app = create_app()

    @app.before_request
    def server_claim():
        g.cpa_principal = Principal("source-stage-admin", "source-stage-admin", "test")
        g.cpa_admin = True

    return app.test_client()


@pytest.mark.parametrize("resource,table,filename,raw,expected", [
    ("dataset", "job_dataset", "jobs.json", b'[{"title":"A"},{"title":"B"}]', 2),
    ("knowledge-base", "knowledge_base", "notes.txt", "技能证据\n".encode(), 0),
])
def test_source_staging_read_integrity_and_delete(
    admin_client, resource, table, filename, raw, expected
):
    mysql = get_mysql_client()
    source_id = None
    file_path = None
    try:
        uploaded = admin_client.post(
            f"/api/admin/{resource}/upload",
            data={"title": "私有暂存", "file": (io.BytesIO(raw), filename),
                  "status": "completed", "count": "999"},
        )
        assert uploaded.status_code == 200, uploaded.json
        data = uploaded.json["data"]
        source_id = data["id"]
        assert data["status"] == "pending"
        assert data["content_staged"] is True
        assert data["content_ingested"] is False
        assert data["staged_record_count"] == expected
        assert data["content_sha256"] == hashlib.sha256(raw).hexdigest()

        rows = mysql.execute(f"SELECT * FROM {table} WHERE id=:id", {"id": source_id})
        assert len(rows) == 1
        row = rows[0]
        file_path = Path(row["file_path"])
        assert file_path.read_bytes() == raw
        assert row["file_size"] == len(raw)
        assert row["content_sha256"] == data["content_sha256"]
        assert row["status"] == "pending"
        assert row["uploaded_by"] == "source-stage-admin"
        if resource == "dataset":
            assert row["count"] == 0
            assert row["staged_record_count"] == 2

        fetched = admin_client.get(f"/api/admin/{resource}/{source_id}/content")
        assert fetched.status_code == 200, fetched.json
        assert fetched.json["data"]["published"] is False
        assert fetched.json["data"]["graph_built"] is False
        if resource == "dataset":
            assert [item["title"] for item in fetched.json["data"]["content"]] == ["A", "B"]
        else:
            assert fetched.json["data"]["content"] == "技能证据\n"

        # One changed variable: tamper the private bytes. Read must not present it as verified.
        file_path.write_bytes(raw + b"x")
        corrupt = admin_client.get(f"/api/admin/{resource}/{source_id}/content")
        assert corrupt.status_code == 503
        assert corrupt.json["code"] == "admin_source_unavailable"

        deleted = admin_client.delete(f"/api/admin/{resource}/{source_id}")
        assert deleted.status_code == 200, deleted.json
        assert mysql.execute(f"SELECT id FROM {table} WHERE id=:id", {"id": source_id}) == []
        assert not file_path.exists()
        source_id = None
    finally:
        if source_id:
            mysql.execute_write(f"DELETE FROM {table} WHERE id=:id", {"id": source_id})
        if file_path:
            file_path.unlink(missing_ok=True)


@pytest.mark.parametrize("resource,table,filename,raw", [
    ("dataset", "job_dataset", "jobs.json", b'[{"name":"not a title"}]'),
    ("knowledge-base", "knowledge_base", "notes.txt", b"\xff"),
])
def test_invalid_source_and_database_failure_leave_no_file_or_row(
    admin_client, monkeypatch, tmp_path, resource, table, filename, raw
):
    mysql = get_mysql_client()
    bad = admin_client.post(f"/api/admin/{resource}/upload",
                            data={"title": "bad", "file": (io.BytesIO(raw), filename)})
    assert bad.status_code == 400, bad.json
    assert not list((tmp_path / "admin_sources").glob("*"))

    token = uuid.uuid4().hex[:12]
    source_id = ("ds_" if resource == "dataset" else "kb_") + token
    good_raw = b'[{"title":"A"}]' if resource == "dataset" else b"valid note"

    class FailingEngine:
        def begin(self):
            raise RuntimeError("injected database transaction failure")

    class FailingMysql:
        engine = FailingEngine()

    with monkeypatch.context() as patch:
        patch.setattr("app.api.admin.uuid.uuid4", lambda: SimpleNamespace(hex=token))
        patch.setattr("app.api.admin.get_mysql_client", lambda: FailingMysql())
        failed = admin_client.post(f"/api/admin/{resource}/upload",
                                   data={"title": "DB failure",
                                         "file": (io.BytesIO(good_raw), filename)})
    assert failed.status_code == 500
    assert mysql.execute(f"SELECT id FROM {table} WHERE id=:id", {"id": source_id}) == []
    assert not list((tmp_path / "admin_sources").glob("*"))


def test_staged_content_remains_admin_only(admin_client, monkeypatch):
    denied_app = create_app()

    @denied_app.before_request
    def student_claim():
        g.cpa_principal = Principal("ordinary", "ordinary", "test")

    denied = denied_app.test_client().post(
        "/api/admin/dataset/upload",
        data={"title": "no", "file": (io.BytesIO(b'[{"title":"A"}]'), "jobs.json")},
    )
    assert denied.status_code == 403


def test_dataset_size_limit_and_delete_transaction_failure_restore(admin_client, monkeypatch, tmp_path):
    mysql = get_mysql_client()
    # Keep the same boundary code path without spilling a 2 MiB multipart to
    # the host's nearly full Windows temporary volume.
    with monkeypatch.context() as patch:
        patch.setattr("app.services.admin_source_store.MAX_SOURCE_BYTES", 8)
        oversized = admin_client.post(
            "/api/admin/dataset/upload",
            data={"title": "too big", "file": (io.BytesIO(b"123456789"), "jobs.json")},
        )
    assert oversized.status_code == 400
    assert not list((tmp_path / "admin_sources").glob("*"))

    uploaded = admin_client.post(
        "/api/admin/dataset/upload",
        data={"title": "rollback", "file": (io.BytesIO(b'[{"title":"A"}]'), "jobs.json")},
    )
    assert uploaded.status_code == 200
    source_id = uploaded.json["data"]["id"]
    path = Path(mysql.execute("SELECT file_path FROM job_dataset WHERE id=:id", {"id": source_id})[0]["file_path"])

    class FailingEngine:
        def begin(self):
            raise RuntimeError("injected delete transaction failure")

    class FailingMysql:
        engine = FailingEngine()
        execute = mysql.execute

    try:
        with monkeypatch.context() as patch:
            patch.setattr("app.api.admin.get_mysql_client", lambda: FailingMysql())
            failed = admin_client.delete(f"/api/admin/dataset/{source_id}")
        assert failed.status_code == 500
        assert path.read_bytes() == b'[{"title":"A"}]'
        assert mysql.execute("SELECT id FROM job_dataset WHERE id=:id", {"id": source_id})

        deleted = admin_client.delete(f"/api/admin/dataset/{source_id}")
        assert deleted.status_code == 200
        assert not path.exists()
    finally:
        mysql.execute_write("DELETE FROM job_dataset WHERE id=:id", {"id": source_id})
        path.unlink(missing_ok=True)


def test_persisted_path_cannot_redirect_admin_content_read(admin_client, tmp_path):
    mysql = get_mysql_client()
    uploaded = admin_client.post(
        "/api/admin/knowledge-base/upload",
        data={"title": "path fence", "file": (io.BytesIO(b"safe"), "note.txt")},
    )
    assert uploaded.status_code == 200
    source_id = uploaded.json["data"]["id"]
    original = Path(mysql.execute("SELECT file_path FROM knowledge_base WHERE id=:id", {"id": source_id})[0]["file_path"])
    outside = tmp_path / "outside.txt"
    outside.write_text("not for admin read", encoding="utf-8")
    try:
        mysql.execute_write("UPDATE knowledge_base SET file_path=:path WHERE id=:id",
                            {"path": str(outside), "id": source_id})
        blocked = admin_client.get(f"/api/admin/knowledge-base/{source_id}/content")
        assert blocked.status_code == 503
        assert "not for admin read" not in str(blocked.json)
    finally:
        mysql.execute_write("UPDATE knowledge_base SET file_path=:path WHERE id=:id",
                            {"path": str(original), "id": source_id})
        admin_client.delete(f"/api/admin/knowledge-base/{source_id}")
        original.unlink(missing_ok=True)


def test_real_socket_http_staging_my_sql_and_file_roundtrip(admin_client):
    """Exercise the registered Flask route over TCP, not only test_client dispatch."""
    mysql = get_mysql_client()
    server = make_server("127.0.0.1", 0, admin_client.application, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/api/admin/knowledge-base"
    source_id = None
    path = None
    try:
        uploaded = requests.post(
            base + "/upload", data={"title": "socket source"},
            files={"file": ("source.txt", b"socket source bytes", "text/plain")}, timeout=10,
        )
        assert uploaded.status_code == 200, uploaded.text
        source_id = uploaded.json()["data"]["id"]
        row = mysql.execute("SELECT file_path, content_sha256 FROM knowledge_base WHERE id=:id",
                            {"id": source_id})[0]
        path = Path(row["file_path"])
        assert path.read_bytes() == b"socket source bytes"
        assert row["content_sha256"] == hashlib.sha256(b"socket source bytes").hexdigest()
        fetched = requests.get(base + f"/{source_id}/content", timeout=10)
        assert fetched.status_code == 200
        assert fetched.json()["data"]["content"] == "socket source bytes"
        deleted = requests.delete(base + f"/{source_id}", timeout=10)
        assert deleted.status_code == 200
        assert mysql.execute("SELECT id FROM knowledge_base WHERE id=:id", {"id": source_id}) == []
        assert not path.exists()
        source_id = None
    finally:
        server.shutdown()
        thread.join(timeout=10)
        if source_id:
            mysql.execute_write("DELETE FROM knowledge_base WHERE id=:id", {"id": source_id})
        if path:
            path.unlink(missing_ok=True)
