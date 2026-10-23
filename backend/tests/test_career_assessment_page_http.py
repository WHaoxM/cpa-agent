"""The real Exams page payload is a gap report, not invented skill levels."""

import json
import os
import threading
import uuid

import pytest
import requests
from flask import Flask, g
from werkzeug.serving import make_server

from app.api import career_bp
from app.api import career as career_api


class AssessmentStore:
    def __init__(self):
        self.queries = []
        self.writes = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        if "SELECT assessment_id FROM student_self_assessment" in sql:
            return [{"assessment_id": params["assessment_id"]}]
        if "FROM career_insights ci" in sql:
            return [{"role_name": "后端开发", "category": "软件工程", "matched_skill_count": 1}]
        return []

    def execute_write(self, sql, params=None):
        self.writes.append(params)
        return 1


def _socket_app():
    app = Flask(__name__)
    app.register_blueprint(career_bp, url_prefix="/api/career")

    @app.before_request
    def bind_owner():
        g.cpa_principal = {"principal_id": "test-account", "student_id": "owner_A"}

    return app


def test_exams_gap_payload_persists_without_inventing_levels(monkeypatch):
    store = AssessmentStore()
    monkeypatch.setattr(career_api, "get_mysql_client", lambda: store)
    monkeypatch.setattr(career_api, "_cache_delete", lambda _key: None)
    server = make_server("127.0.0.1", 0, _socket_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = requests.post(
            f"http://127.0.0.1:{server.server_port}/api/career/assessment",
            json={
                "student_id": "forged_other_owner",
                "assessed_skills": ["Python", "SQL"],
                "assessed_abilities": {"py": 25, "sql": 75},
                "target_role": "后端开发",
            },
            timeout=10,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "partial"
        assert body["data"]["weak_skills"] == ["Python", "SQL"]
        assert body["data"]["known_skills"] == []
        assert body["data"]["recommended_roles"] == []
        assert store.writes[0]["student_id"] == "owner_A"
        skills = json.loads(store.writes[0]["assessed_skills"])
        assert [item["skill"] for item in skills] == ["Python", "SQL"]
        assert all(item["level"] is None for item in skills)
        assert not any("FROM career_insights ci" in sql for sql in store.queries)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_no_gap_is_valid_when_answer_scores_exist(monkeypatch):
    store = AssessmentStore()
    monkeypatch.setattr(career_api, "get_mysql_client", lambda: store)
    monkeypatch.setattr(career_api, "_cache_delete", lambda _key: None)
    client = _socket_app().test_client()
    response = client.post("/api/career/assessment", json={
        "assessed_skills": [], "assessed_abilities": {"py": 100},
    })
    assert response.status_code == 200
    assert response.get_json()["data"]["weak_skills"] == []
    assert json.loads(store.writes[0]["assessed_skills"]) == []


def test_gap_payload_rejects_invalid_scores_before_database(monkeypatch):
    monkeypatch.setattr(
        career_api, "get_mysql_client",
        lambda: (_ for _ in ()).throw(AssertionError("invalid answers must not reach DB")),
    )
    client = _socket_app().test_client()
    for scores in ({"py": 101}, {"py": False}, {"py": "75"}, {}):
        response = client.post("/api/career/assessment", json={
            "assessed_skills": ["Python"], "assessed_abilities": scores,
        })
        assert response.status_code == 400


def test_structured_assessment_recommendation_uses_skill_match_not_market_prior(monkeypatch):
    store = AssessmentStore()
    monkeypatch.setattr(career_api, "get_mysql_client", lambda: store)
    monkeypatch.setattr(career_api, "_cache_delete", lambda _key: None)
    client = _socket_app().test_client()
    response = client.post("/api/career/assessment", json={
        "assessed_skills": [{"skill": "Python", "level": "熟练"}],
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["status"] == "partial"
    assert response.get_json()["partial_reasons"] == ["unverified_seed_catalogue"]
    assert response.get_json()["data"]["recommended_roles"] == [
        {"role_name": "后端开发", "category": "软件工程", "matched_skill_count": 1}
    ]
    sql = next(sql for sql in store.queries if "FROM career_insights ci" in sql)
    assert "demand_level" not in sql
    assert "avg_salary" not in sql
    assert "ORDER BY" in sql


def test_historical_assessment_read_strips_seed_market_claims_and_old_cache(monkeypatch):
    store = AssessmentStore()

    def read(sql, params=None):
        store.queries.append(sql)
        return [{
            "assessment_id": "asm_old", "student_id": "owner_A",
            "assessed_skills": '[{"skill":"Python","level":"熟练"}]',
            "assessed_abilities": '{}', "weak_skills": '[]',
            "recommended_roles": json.dumps([{
                "role_name": "后端开发", "category": "软件工程",
                "avg_salary_high": 50, "demand_level": "极高",
            }, {
                "role_name": "已撤回岗位", "source": "published_job",
                "job_portrait_id": "job_old", "publication_version": 1,
                "content_sha256": "a" * 64,
            }]),
        }]

    store.execute = read
    monkeypatch.setattr(career_api, "get_mysql_client", lambda: store)
    cache_keys = []
    monkeypatch.setattr(career_api, "_cache_get", lambda key: cache_keys.append(key) or None)
    monkeypatch.setattr(career_api, "_cache_set", lambda *_args: None)
    response = _socket_app().test_client().get("/api/career/assessment/owner_A")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "partial"
    assert "legacy_market_claims_removed" in body["partial_reasons"]
    assert body["market_data_status"] == "legacy_unverified_removed"
    assert body["data"]["recommended_roles"] == [
        {"role_name": "后端开发", "category": "软件工程"}
    ]
    # Latest reads must not be replaced by an older Redis projection.
    assert cache_keys == []


def test_generic_latest_assessment_does_not_replay_ext_publication_envelope(monkeypatch):
    store = AssessmentStore()

    def read(sql, params=None):
        return [{
            "assessment_id": "exam_old", "student_id": "owner_A",
            "assessed_skills": '[]', "assessed_abilities": '{}', "weak_skills": '[]',
            "recommended_roles": json.dumps({
                "source": "reviewed_job_catalog", "status": "ready", "reason": None,
                "items": [{"publication_source": "reviewed_job_catalog",
                           "job_portrait_id": "revoked_job", "publication_version": "v1",
                           "content_sha256": "a" * 64, "position_name": "已撤回岗位"}],
            }),
        }]

    store.execute = read
    monkeypatch.setattr(career_api, "get_mysql_client", lambda: store)
    monkeypatch.setattr(career_api, "_cache_get", lambda _key: None)
    monkeypatch.setattr(career_api, "_cache_set", lambda *_args: None)
    response = _socket_app().test_client().get("/api/career/assessment/owner_A")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "partial"
    assert "legacy_market_claims_removed" in body["partial_reasons"]
    assert body["data"]["recommended_roles"] == []


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="opt-in real MySQL roundtrip")
def test_exams_payload_persists_and_reads_from_real_mysql(monkeypatch):
    from app.services.db import get_mysql_client

    owner = "assessment_page_" + uuid.uuid4().hex[:16]
    store = get_mysql_client()
    monkeypatch.setattr(career_api, "_cache_get", lambda _key: None)
    monkeypatch.setattr(career_api, "_cache_set", lambda *_args: None)
    monkeypatch.setattr(career_api, "_cache_delete", lambda _key: None)
    app = Flask(__name__ + "_mysql")
    app.register_blueprint(career_bp, url_prefix="/api/career")

    @app.before_request
    def bind_owner():
        g.cpa_principal = {"principal_id": owner, "student_id": owner}

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assessment_id = None
    try:
        base = f"http://127.0.0.1:{server.server_port}/api/career/assessment"
        posted = requests.post(base, json={
            "assessed_skills": ["Python", "SQL"],
            "assessed_abilities": {"py": 25, "sql": 75},
            "target_role": "后端开发",
        }, timeout=10)
        assert posted.status_code == 200, posted.text
        assessment_id = posted.json()["data"]["assessment_id"]
        loaded = requests.get(base + "/" + owner, timeout=10)
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["status"] == "partial"
        assert loaded.json()["data"]["assessment_id"] == assessment_id
        assert loaded.json()["data"]["weak_skills"] == ["Python", "SQL"]
        rows = store.execute(
            "SELECT student_id, assessed_skills, weak_skills FROM student_self_assessment "
            "WHERE assessment_id = :assessment_id", {"assessment_id": assessment_id},
        )
        assert len(rows) == 1 and rows[0]["student_id"] == owner
    finally:
        if assessment_id:
            store.execute_write(
                "DELETE FROM student_self_assessment WHERE assessment_id = :assessment_id "
                "AND student_id = :student_id",
                {"assessment_id": assessment_id, "student_id": owner},
            )
            assert store.execute(
                "SELECT assessment_id FROM student_self_assessment WHERE assessment_id = :assessment_id",
                {"assessment_id": assessment_id},
            ) == []
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="opt-in real MySQL recommendation query")
def test_named_mastery_recommendation_runs_against_real_mysql(monkeypatch):
    from app.services.db import get_mysql_client

    owner = "assessment_named_" + uuid.uuid4().hex[:16]
    store = get_mysql_client()
    monkeypatch.setattr(career_api, "_cache_delete", lambda _key: None)
    app = Flask(__name__ + "_named")
    app.register_blueprint(career_bp, url_prefix="/api/career")

    @app.before_request
    def bind_owner():
        g.cpa_principal = {"principal_id": owner, "student_id": owner}

    assessment_id = None
    try:
        response = app.test_client().post("/api/career/assessment", json={
            "assessed_skills": [{"skill": "Python", "level": "熟练"}],
        })
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assessment_id = body["data"]["assessment_id"]
        assert body["market_data_status"] == "not_used"
        assert all(
            "avg_salary" not in role and "demand_level" not in role
            for role in body["data"]["recommended_roles"]
        )
        assert store.execute(
            "SELECT assessment_id FROM student_self_assessment "
            "WHERE assessment_id = :assessment_id AND student_id = :student_id",
            {"assessment_id": assessment_id, "student_id": owner},
        )
    finally:
        if assessment_id:
            store.execute_write(
                "DELETE FROM student_self_assessment WHERE assessment_id = :assessment_id "
                "AND student_id = :student_id",
                {"assessment_id": assessment_id, "student_id": owner},
            )
            assert store.execute(
                "SELECT assessment_id FROM student_self_assessment WHERE assessment_id = :assessment_id",
                {"assessment_id": assessment_id},
            ) == []
