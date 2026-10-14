"""Live MySQL + create_app HTTP acceptance for the OOV review state machine."""

import json
import uuid

import pytest
from flask import g

from app import create_app
from app.api import admin as admin_api
from app.api._helpers import Principal
from app.config import Config
from app.services.db import get_mysql_client


def _client(monkeypatch, *, admin):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def identity():
        g.cpa_principal = Principal(
            "operator-test" if admin else "ordinary-test",
            "oov-test-owner",
            "test",
        )
        if admin:
            g.cpa_admin = True

    return app.test_client()


def _row(mysql, oov_id):
    rows = mysql.execute(
        "SELECT oov_id, status, canonical_mapping, reviewed_by, review_notes "
        "FROM oov_entity WHERE oov_id=:id",
        {"id": oov_id},
    )
    return rows[0] if rows else None


@pytest.mark.parametrize(("action", "body"), [
    ("approve", ["bad"]),
    ("approve", {"canonical_name": ["bad"]}),
    ("approve", {"canonical_name": "valid", "aliases": "not-a-list"}),
    ("approve", {"canonical_name": "valid", "category": ["bad"]}),
    ("reject", ["bad"]),
    ("reject", {"reason": ["bad"]}),
])
def test_oov_review_rejects_malformed_input_before_mysql(monkeypatch, action, body):
    client = _client(monkeypatch, admin=True)

    def unexpected_mysql():
        raise AssertionError("invalid input must not access MySQL")

    monkeypatch.setattr(admin_api, "get_mysql_client", unexpected_mysql)
    response = client.post(f"/api/admin/oov/nonexistent/{action}", json=body)
    assert response.status_code == 400
    assert response.json["success"] is False


def test_oov_pending_filtered_total_matches_filtered_rows(monkeypatch):
    mysql = get_mysql_client()
    token = uuid.uuid4().hex[:12]
    skill_id, major_id = "oov_filter_skill_" + token, "oov_filter_major_" + token
    client = _client(monkeypatch, admin=True)
    try:
        for oov_id, entity_type in ((skill_id, "skill"), (major_id, "major")):
            mysql.execute_write(
                "INSERT INTO oov_entity (oov_id, raw_entity, entity_type, context_json, source) "
                "VALUES (:id, :raw, :kind, '{}', 'oov-filter-test')",
                {"id": oov_id, "raw": "filter_" + token, "kind": entity_type},
            )
        expected = mysql.execute(
            "SELECT COUNT(*) AS n FROM oov_entity "
            "WHERE status='pending' AND entity_type='skill'"
        )[0]["n"]
        response = client.get("/api/admin/oov/pending?entity_type=skill&limit=1")
        assert response.status_code == 200
        assert response.json["data"]["total"] == expected
    finally:
        mysql.execute_write(
            "DELETE FROM oov_entity WHERE oov_id IN (:a, :b)",
            {"a": skill_id, "b": major_id},
        )
        assert mysql.execute(
            "SELECT COUNT(*) AS n FROM oov_entity WHERE oov_id IN (:a, :b)",
            {"a": skill_id, "b": major_id},
        )[0]["n"] == 0


def test_oov_review_requires_server_admin_and_only_transitions_pending_once(monkeypatch):
    mysql = get_mysql_client()
    suffix = uuid.uuid4().hex[:10]
    reject_id, approve_id = "oov_rej_" + suffix, "oov_app_" + suffix
    canonical_rejected, canonical_approved = "OOVRejected_" + suffix, "OOVApproved_" + suffix
    admin = _client(monkeypatch, admin=True)
    ordinary = _client(monkeypatch, admin=False)
    try:
        for oov_id, raw in ((reject_id, "synthetic reject " + suffix),
                            (approve_id, "synthetic approve " + suffix)):
            mysql.execute_write(
                "INSERT INTO oov_entity (oov_id, raw_entity, entity_type, context_json, source) "
                "VALUES (:id, :raw, 'skill', :context, 'oov-state-test')",
                {"id": oov_id, "raw": raw, "context": json.dumps({"fixture": suffix})},
            )

        for client, method, path, body in (
            (ordinary, "post", f"/api/admin/oov/{reject_id}/reject", {"reason": "deny"}),
            (ordinary, "post", f"/api/admin/oov/{approve_id}/approve", {"canonical_name": canonical_approved}),
        ):
            response = getattr(client, method)(path, json=body)
            assert response.status_code == 403
        assert _row(mysql, reject_id)["status"] == "pending"
        assert _row(mysql, approve_id)["status"] == "pending"

        missing_reject = admin.post("/api/admin/oov/missing_" + suffix + "/reject", json={})
        missing_approve = admin.post(
            "/api/admin/oov/missing_" + suffix + "/approve",
            json={"canonical_name": canonical_approved},
        )

        rejected = admin.post(f"/api/admin/oov/{reject_id}/reject", json={
            "reason": "confirmed synthetic", "reviewed_by": "forged-operator",
        })
        repeat_reject = admin.post(f"/api/admin/oov/{reject_id}/reject", json={"reason": "changed"})
        approve_rejected = admin.post(
            f"/api/admin/oov/{reject_id}/approve", json={"canonical_name": canonical_rejected}
        )

        approved = admin.post(
            f"/api/admin/oov/{approve_id}/approve",
            json={"canonical_name": canonical_approved, "aliases": ["synthetic-alias"],
                  "reviewed_by": "forged-operator"},
        )
        repeat_approve = admin.post(
            f"/api/admin/oov/{approve_id}/approve",
            json={"canonical_name": canonical_approved + "Changed"},
        )
        reject_approved = admin.post(
            f"/api/admin/oov/{approve_id}/reject", json={"reason": "must not reverse approval"}
        )

        rejected_state = _row(mysql, reject_id)
        approved_state = _row(mysql, approve_id)
        canonical_rows = mysql.execute(
            "SELECT canonical_name FROM canonical_entity WHERE source=:source ORDER BY id",
            {"source": f"oov_approved:{approve_id}"},
        )
        observations = {
            "missing_reject": missing_reject.status_code,
            "missing_approve": missing_approve.status_code,
            "reject": rejected.status_code,
            "repeat_reject": repeat_reject.status_code,
            "approve_rejected": approve_rejected.status_code,
            "approved": approved.status_code,
            "repeat_approve": repeat_approve.status_code,
            "reject_approved": reject_approved.status_code,
            "rejected_status": rejected_state["status"],
            "rejected_reviewed_by": rejected_state["reviewed_by"],
            "approved_status": approved_state["status"],
            "approved_reviewed_by": approved_state["reviewed_by"],
            "approved_mapping": approved_state["canonical_mapping"],
            "canonical_names": [row["canonical_name"] for row in canonical_rows],
        }
        assert observations == {
            "missing_reject": 404,
            "missing_approve": 404,
            "reject": 200,
            "repeat_reject": 409,
            "approve_rejected": 409,
            "approved": 200,
            "repeat_approve": 409,
            "reject_approved": 409,
            "rejected_status": "rejected",
            "rejected_reviewed_by": "operator-test",
            "approved_status": "approved",
            "approved_reviewed_by": "operator-test",
            "approved_mapping": canonical_approved,
            "canonical_names": [canonical_approved],
        }, observations
    finally:
        mysql.execute_write(
            "DELETE FROM canonical_entity WHERE source IN (:reject_source, :approve_source)",
            {"reject_source": f"oov_approved:{reject_id}",
             "approve_source": f"oov_approved:{approve_id}"},
        )
        mysql.execute_write("DELETE FROM oov_entity WHERE oov_id IN (:a, :b)",
                            {"a": reject_id, "b": approve_id})
        assert mysql.execute(
            "SELECT COUNT(*) AS n FROM canonical_entity WHERE source IN (:a, :b)",
            {"a": f"oov_approved:{reject_id}", "b": f"oov_approved:{approve_id}"},
        )[0]["n"] == 0
        assert mysql.execute(
            "SELECT COUNT(*) AS n FROM oov_entity WHERE oov_id IN (:a, :b)",
            {"a": reject_id, "b": approve_id},
        )[0]["n"] == 0
