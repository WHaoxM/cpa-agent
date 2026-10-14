"""Live MySQL acceptance for ordinary admin catalog metadata routes."""

import uuid
from types import SimpleNamespace

import pytest
from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config
from app.services.db import get_mysql_client


@pytest.fixture
def admin_client(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def inject_server_claim():
        g.cpa_principal = Principal("metadata-admin", "metadata-test-owner", "test")
        g.cpa_admin = True

    return app.test_client()


@pytest.mark.parametrize(
    ("resource", "table", "id_prefix", "payload"),
    [
        ("dataset", "job_dataset", "ds_", {"category": "test"}),
        ("knowledge-base", "knowledge_base", "kb_", {"type": "text"}),
    ],
)
def test_admin_metadata_upload_delete_and_missing_delete_are_verified(
    admin_client, monkeypatch, resource, table, id_prefix, payload
):
    mysql = get_mysql_client()
    token = uuid.uuid4().hex
    title = f"metadata_acceptance_{token}"
    upload_path = f"/api/admin/{resource}/upload"
    item_path = f"/api/admin/{resource}"
    missing_id = f"{id_prefix}missing_{token}"
    created_id = None

    denied_app = create_app()
    @denied_app.before_request
    def inject_non_admin():
        g.cpa_principal = Principal("ordinary-user", "metadata-test-owner", "test")

    try:
        denied = denied_app.test_client().post(upload_path, json={"title": title, **payload})
        assert denied.status_code == 403
        assert denied.json["success"] is False

        # Missing title must be rejected before storage and create no row.
        failure_id = f"{id_prefix}{token[:12]}"
        with monkeypatch.context() as patch:
            patch.setattr("app.api.admin.uuid.uuid4", lambda: SimpleNamespace(hex=token))
            failed = admin_client.post(upload_path, json={**payload})
        assert failed.status_code == 400
        assert failed.json["success"] is False
        assert mysql.execute(f"SELECT id FROM {table} WHERE id=:id", {"id": failure_id}) == []

        uploaded = admin_client.post(upload_path, json={"title": title, **payload})
        assert uploaded.status_code == 200
        assert uploaded.json["success"] is True
        created_id = uploaded.json["data"]["id"]
        assert created_id.startswith(id_prefix)
        saved = mysql.execute(f"SELECT id, title FROM {table} WHERE id=:id", {"id": created_id})
        assert saved == [{"id": created_id, "title": title}]

        missing_delete = admin_client.delete(f"{item_path}/{missing_id}")
        assert missing_delete.status_code == 404
        assert missing_delete.json["success"] is False
        assert mysql.execute(f"SELECT id FROM {table} WHERE id=:id", {"id": missing_id}) == []

        deleted = admin_client.delete(f"{item_path}/{created_id}")
        assert deleted.status_code == 200
        assert deleted.json["success"] is True
        assert mysql.execute(f"SELECT id FROM {table} WHERE id=:id", {"id": created_id}) == []
    finally:
        if created_id:
            mysql.execute_write(f"DELETE FROM {table} WHERE id=:id", {"id": created_id})
