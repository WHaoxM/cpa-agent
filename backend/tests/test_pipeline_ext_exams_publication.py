"""The legacy exams route must not recommend unpublished job portraits."""

import json
import hashlib
import os
import threading
import uuid

import pytest
import requests
from flask import Flask, g
from werkzeug.serving import make_server

from app.api import pipeline_ext_bp


def _snapshot(name):
    source = f"{name}. Bachelor required. Python required. No major restriction. No certificates."
    return {
        "position_name": name, "source_uri": "https://example.com/exam-test-job",
        "source_text": source, "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_fetched_at": "2026-10-08T00:00:00Z", "source_license": "test-only",
        "reviewed_at": "2026-10-09T00:00:00Z", "reviewed_by": "test",
        "review_state": "approved", "required_skills": [{"name": "Python"}],
        "job_profile": {"degree_requirement": "本科", "major_requirements": [],
                        "major_strict": False, "required_certs": []},
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": name},
            "degree_requirement": {"status": "explicit", "excerpt": "Bachelor required"},
            "major_requirements": {"status": "explicit", "excerpt": "No major restriction"},
            "major_strict": {"status": "explicit", "excerpt": "No major restriction"},
            "required_certs": {"status": "explicit", "excerpt": "No certificates"},
            "required_skills": {"status": "explicit", "excerpt": "Python required"},
        },
    }


def _app(owner):
    app = Flask(__name__)
    app.register_blueprint(pipeline_ext_bp, url_prefix="/api/pipeline/ext")

    @app.before_request
    def bind_owner():
        g.cpa_principal = {"principal_id": owner, "student_id": owner}

    return app


class _UnpublishedStore:
    def __init__(self):
        self.assessments = {}
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        if "FROM job_portrait_4d" in sql:
            return [{"position_name": "Unreviewed Python Engineer",
                     "portrait_json": '{"skill":"Python"}'}]
        if "FROM student_self_assessment" in sql:
            row = self.assessments.get(params["assessment_id"])
            return [row] if row and row["student_id"] == params["student_id"] else []
        return []

    def execute_write(self, sql, params=None):
        self.assessments[params["assessment_id"]] = {
            **params, "created_at": None,
        }

    def fetch_all(self, sql, params=None):
        if "row_count" in sql:
            return [{"row_count": 0, "max_id": 0, "published_id_sum": 0,
                     "revoked_id_sum": 0}]
        if "COUNT(*) AS n" in sql:
            return [{"n": 0, "latest_published_at": None}]
        if "latest.latest_id=p.id" in sql:
            return []
        raise AssertionError(sql)


def test_unpublished_portrait_is_never_recommended_on_http_or_historical_get(monkeypatch):
    store = _UnpublishedStore()
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: store)
    app = _app("exam_owner")
    response = app.test_client().post("/api/pipeline/ext/exams", json={
        "answers": [{"skill": "Python", "level": 4}],
        "target_role": "Python",
    })
    assert response.status_code == 200, response.get_json()
    body = response.get_json()["data"]
    assert body["recommended_roles"] == []
    assert body["recommendation_status"] == "no_published_match"
    assert all("FROM job_portrait_4d" not in sql for sql in store.queries)
    loaded = app.test_client().get(f"/api/pipeline/ext/exams/{body['assessment_id']}")
    assert loaded.status_code == 200, loaded.get_json()
    assert loaded.get_json()["data"]["recommended_roles"] == []
    store.assessments[body["assessment_id"]]["recommended_roles"] = json.dumps(
        ["Unreviewed Python Engineer"]
    )
    legacy = app.test_client().get(f"/api/pipeline/ext/exams/{body['assessment_id']}")
    assert legacy.get_json()["data"]["recommended_roles"] == []
    assert legacy.get_json()["data"]["recommendation_status"] == "stale_or_unverified_removed"


def test_catalog_unavailable_is_explicit_not_false_no_result(monkeypatch):
    store = _UnpublishedStore()
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: store)
    monkeypatch.setattr(
        "app.services.job_catalog_listing.list_published_job_candidates",
        lambda *_args, **_kwargs: {"status": "unavailable", "reason": "catalog_database_unavailable"},
    )
    response = _app("exam_owner").test_client().post("/api/pipeline/ext/exams", json={
        "answers": [{"skill": "Python", "level": 4}],
    })
    assert response.status_code == 200
    assert response.get_json()["data"]["recommended_roles"] == []
    assert response.get_json()["data"]["recommendation_status"] == "unavailable"
    assert response.get_json()["data"]["recommendation_reason"] == "catalog_database_unavailable"
    saved = json.loads(next(iter(store.assessments.values()))["recommended_roles"])
    assert saved["items"] == []
    assert saved["status"] == "unavailable"
    saved_id = next(iter(store.assessments))
    loaded = _app("exam_owner").test_client().get(f"/api/pipeline/ext/exams/{saved_id}")
    assert loaded.get_json()["data"]["recommendation_status"] == "unavailable"
    assert loaded.get_json()["data"]["recommendation_reason"] == "catalog_database_unavailable"


def test_verified_publication_is_recommended_and_history_rechecks_version(monkeypatch):
    from app.services.job_publication import content_sha256

    store = _UnpublishedStore()
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: store)
    snap = _snapshot("Exam Verified Python")
    digest = content_sha256(snap)
    current = {"id": 42, "publication_version": "v1", "content_sha256": digest,
               "snapshot_json": snap}
    candidate = {"job_portrait_id": "verified-42", "publication_version": "v1",
                 "content_sha256": digest}
    monkeypatch.setattr(
        "app.services.job_catalog_listing.list_published_job_candidates",
        lambda *_args, **_kwargs: {"status": "ready", "reason": None,
                                  "catalog_version": "fixture-version",
                                  "items": [candidate], "has_more": False},
    )
    monkeypatch.setattr(
        "app.services.job_publication.read_published_job",
        lambda *_args, **_kwargs: current,
    )
    app = _app("exam_owner")
    response = app.test_client().post("/api/pipeline/ext/exams", json={
        "answers": [{"skill": "Python", "level": 4}],
    })
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["recommended_roles"] == [snap["position_name"]]
    assert data["recommendation_status"] == "ready"
    saved = json.loads(store.assessments[data["assessment_id"]]["recommended_roles"])
    assert saved["source"] == "reviewed_job_catalog" and saved["status"] == "ready"
    assert saved["items"] == [{
        "publication_source": "reviewed_job_catalog",
        "job_portrait_id": "verified-42", "publication_version": "v1",
        "content_sha256": digest, "position_name": snap["position_name"],
    }]
    loaded = app.test_client().get(f"/api/pipeline/ext/exams/{data['assessment_id']}")
    assert loaded.get_json()["data"]["recommended_roles"] == [snap["position_name"]]
    monkeypatch.setattr("app.services.job_publication.read_published_job", lambda *_a, **_k: None)
    revoked = app.test_client().get(f"/api/pipeline/ext/exams/{data['assessment_id']}")
    assert revoked.get_json()["data"]["recommended_roles"] == []
    assert revoked.get_json()["data"]["recommendation_status"] == "stale_or_unverified_removed"


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="opt-in isolated real MySQL fixture")
def test_unpublished_portrait_leak_rejected_over_real_socket_and_mysql(monkeypatch):
    from app.services.db import get_mysql_client

    mysql = get_mysql_client()
    token = uuid.uuid4().hex[:16]
    owner = "exam_owner_" + token
    portrait_id = "exam_unpublished_" + token
    role = "Exam Unreviewed Python " + token
    mysql.execute_write(
        "INSERT INTO job_portrait_4d (portrait_id,position_name,portrait_json) "
        "VALUES (:id,:name,:portrait)",
        {"id": portrait_id, "name": role, "portrait": json.dumps({"skill": "Python"})},
    )
    server = make_server("127.0.0.1", 0, _app(owner), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assessment_id = None
    try:
        response = requests.post(
            f"http://127.0.0.1:{server.server_port}/api/pipeline/ext/exams",
            json={"answers": [{"skill": "Python", "level": 4}], "target_role": role},
            timeout=10,
        )
        assert response.status_code == 200, response.text
        assessment_id = response.json()["data"]["assessment_id"]
        assert response.json()["data"]["recommended_roles"] == []
        row = mysql.execute_single(
            "SELECT student_id,recommended_roles FROM student_self_assessment "
            "WHERE assessment_id=:id", {"id": assessment_id},
        )
        assert row["student_id"] == owner
        roles = row["recommended_roles"]
        saved = json.loads(roles) if isinstance(roles, str) else roles
        assert saved["items"] == [] and saved["status"] == "no_published_match"
    finally:
        if assessment_id:
            mysql.execute_write(
                "DELETE FROM student_self_assessment WHERE assessment_id=:id AND student_id=:owner",
                {"id": assessment_id, "owner": owner},
            )
        mysql.execute_write(
            "DELETE FROM job_portrait_4d WHERE portrait_id=:id", {"id": portrait_id},
        )
        server.shutdown()
        thread.join(timeout=5)
        assert mysql.execute("SELECT id FROM job_portrait_4d WHERE portrait_id=:id", {"id": portrait_id}) == []
        if assessment_id:
            assert mysql.execute("SELECT id FROM student_self_assessment WHERE assessment_id=:id",
                                 {"id": assessment_id}) == []


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="opt-in isolated real MySQL fixture")
def test_current_publication_recommended_then_revocation_hidden_on_historical_http():
    from app.services.db import get_mysql_client
    from app.services.job_publication import publish_job

    mysql = get_mysql_client()
    token = uuid.uuid4().hex[:16]
    owner = "exam_published_" + token
    portrait_id = "exam_published_" + token
    role = "Exam Published Python " + token
    publication = publish_job(mysql, job_portrait_id=portrait_id,
                              version="v1", snapshot=_snapshot(role))
    server = make_server("127.0.0.1", 0, _app(owner), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assessment_id = None
    try:
        base = f"http://127.0.0.1:{server.server_port}/api/pipeline/ext/exams"
        posted = requests.post(base, json={"answers": [{"skill": "Python", "level": 4}],
                                           "target_role": role}, timeout=10)
        assert posted.status_code == 200, posted.text
        data = posted.json()["data"]
        assessment_id = data["assessment_id"]
        assert data["recommended_roles"] == [role]
        assert data["recommendation_status"] == "ready"
        stored = mysql.execute_single(
            "SELECT recommended_roles FROM student_self_assessment WHERE assessment_id=:id",
            {"id": assessment_id},
        )["recommended_roles"]
        refs = json.loads(stored) if isinstance(stored, str) else stored
        assert refs["source"] == "reviewed_job_catalog" and refs["status"] == "ready"
        assert refs["items"] == [{"publication_source": "reviewed_job_catalog",
                                   "job_portrait_id": portrait_id, "publication_version": "v1",
                                   "content_sha256": publication["content_sha256"],
                                   "position_name": role}]
        loaded = requests.get(base + "/" + assessment_id, timeout=10)
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["data"]["recommended_roles"] == [role]
        mysql.execute_write("UPDATE job_catalog_publication SET status='revoked' WHERE id=:id",
                            {"id": publication["id"]})
        revoked = requests.get(base + "/" + assessment_id, timeout=10)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["data"]["recommended_roles"] == []
        assert revoked.json()["data"]["recommendation_status"] == "stale_or_unverified_removed"
    finally:
        if assessment_id:
            mysql.execute_write(
                "DELETE FROM student_self_assessment WHERE assessment_id=:id AND student_id=:owner",
                {"id": assessment_id, "owner": owner},
            )
        mysql.execute_write("DELETE FROM job_catalog_publication WHERE job_portrait_id=:id",
                            {"id": portrait_id})
        server.shutdown()
        thread.join(timeout=5)
        assert mysql.execute("SELECT id FROM job_catalog_publication WHERE job_portrait_id=:id",
                             {"id": portrait_id}) == []
        if assessment_id:
            assert mysql.execute("SELECT id FROM student_self_assessment WHERE assessment_id=:id",
                                 {"id": assessment_id}) == []
