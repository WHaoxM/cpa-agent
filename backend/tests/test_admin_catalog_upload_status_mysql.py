"""Metadata upload routes must not accept client-asserted processing states."""

import uuid

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
    ("resource", "table", "prefix", "extra"),
    [
        ("dataset", "job_dataset", "ds_", {"category": "audit", "count": 1}),
        ("knowledge-base", "knowledge_base", "kb_", {"type": "text"}),
    ],
)
def test_metadata_upload_ignores_client_completed_status(
    admin_client, resource, table, prefix, extra
):
    """Metadata persistence is not evidence of content ingestion or completion."""
    mysql = get_mysql_client()
    token = uuid.uuid4().hex[:12]
    title = f"metadata_status_{token}"
    created_id = None
    try:
        response = admin_client.post(
            f"/api/admin/{resource}/upload",
            json={"title": title, "status": "completed", **extra},
        )
        assert response.status_code == 200
        assert response.json["success"] is True
        assert response.json["data"]["status"] == "pending"
        assert response.json["data"]["content_ingested"] is False
        created_id = response.json["data"]["id"]
        assert created_id.startswith(prefix)

        persisted = mysql.execute(
            f"SELECT id, title, status{', count' if resource == 'dataset' else ''} "
            f"FROM {table} WHERE id=:id",
            {"id": created_id},
        )
        expected = {"id": created_id, "title": title, "status": "pending"}
        if resource == "dataset":
            expected["count"] = 0  # No job rows were ingested by metadata upload.
        assert persisted == [expected]

        listed = admin_client.get(f"/api/admin/{resource}")
        assert listed.status_code == 200
        listed_row = next(row for row in listed.json["data"] if row["id"] == created_id)
        assert listed_row["status"] == "pending"
    finally:
        if created_id:
            mysql.execute_write(f"DELETE FROM {table} WHERE id=:id", {"id": created_id})
