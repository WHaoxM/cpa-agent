from flask import g

from app import create_app
from app.api import career as career_api
from app.api import learning as learning_api
from app.config import Config


def _client(monkeypatch, *, principal=True):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()
    if principal:
        @app.before_request
        def bind_principal():
            g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}
    return app.test_client()


def test_career_assessment_rejects_foreign_path_before_cache_or_db(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(career_api, "_cache_get", lambda key: (_ for _ in ()).throw(AssertionError("cache accessed")))
    monkeypatch.setattr(career_api, "get_mysql_client", lambda: (_ for _ in ()).throw(AssertionError("DB accessed")))
    assert client.get("/api/career/assessment/victim_B").status_code == 403


def test_career_assessment_requires_principal_before_write(monkeypatch):
    client = _client(monkeypatch, principal=False)
    monkeypatch.setattr(career_api, "get_mysql_client", lambda: (_ for _ in ()).throw(AssertionError("DB accessed")))
    assert client.post("/api/career/assessment", json={
        "student_id": "victim_B", "assessed_skills": [{"skill": "Python", "level": "了解"}],
    }).status_code == 401


def test_career_assessment_rejects_malformed_skill_items_before_db(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        career_api, "get_mysql_client",
        lambda: (_ for _ in ()).throw(AssertionError("DB accessed")),
    )
    result = client.post("/api/career/assessment", json={
        "assessed_skills": ["not-a-skill"],
    })
    assert result.status_code == 400
    assert result.get_json()["success"] is False


def test_career_assessment_binds_owner_and_only_returns_verified_id(monkeypatch):
    client = _client(monkeypatch)
    writes = []

    class FakeMySQL:
        def execute(self, sql, params=None):
            if "SELECT assessment_id FROM student_self_assessment" in sql:
                return [{"assessment_id": params["assessment_id"]}]
            return []

        def execute_write(self, sql, params=None):
            writes.append(params)

    monkeypatch.setattr(career_api, "get_mysql_client", FakeMySQL)
    result = client.post("/api/career/assessment", json={
        "student_id": "victim_B", "assessed_skills": [{"skill": "Python", "level": "了解"}],
    })
    assert result.status_code == 200
    assert writes[0]["student_id"] == "owner_A"
    assert result.get_json()["data"]["assessment_id"] == writes[0]["assessment_id"]


def test_career_assessment_no_success_without_owned_readback(monkeypatch):
    client = _client(monkeypatch)

    class FakeMySQL:
        def execute(self, sql, params=None):
            if "FROM course c JOIN chapter ch" in sql:
                return [{"course_id": params["course_id"], "chapter_id": params["chapter_id"]}]
            return []

        def execute_write(self, sql, params=None):
            return []

    monkeypatch.setattr(career_api, "get_mysql_client", FakeMySQL)
    result = client.post("/api/career/assessment", json={
        "assessed_skills": [{"skill": "Python", "level": "了解"}],
    })
    assert result.status_code == 500
    assert result.get_json()["success"] is False


def test_learning_progress_rejects_orphan_source_before_write(monkeypatch):
    client = _client(monkeypatch)
    writes = []

    class FakeMySQL:
        def execute(self, sql, params=None):
            return []

        def execute_write(self, sql, params=None):
            writes.append((sql, params))

    monkeypatch.setattr(learning_api, "get_mysql_client", FakeMySQL)
    result = client.post("/api/learning/learning/progress", json={
        "course_id": "missing", "chapter_id": "999", "progress": 50,
    })
    assert result.status_code == 404
    assert result.get_json()["status"] == "source_not_found"
    assert writes == []


def test_learning_progress_rejects_invalid_percentage_before_db(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        learning_api, "get_mysql_client",
        lambda: (_ for _ in ()).throw(AssertionError("DB accessed")),
    )
    result = client.post("/api/learning/learning/progress", json={
        "course_id": "c1", "chapter_id": "1", "progress": 101,
    })
    assert result.status_code == 400


def test_learning_progress_rejects_non_boolean_completed_before_db(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        learning_api, "get_mysql_client",
        lambda: (_ for _ in ()).throw(AssertionError("DB accessed")),
    )
    result = client.post("/api/learning/learning/progress", json={
        "course_id": "c1", "chapter_id": "1", "progress": 25, "completed": "false",
    })
    assert result.status_code == 400
    assert result.get_json()["success"] is False


def test_learning_progress_no_success_without_persisted_state(monkeypatch):
    client = _client(monkeypatch)

    class FakeMySQL:
        def execute(self, sql, params=None):
            if "FROM course c JOIN chapter ch" in sql:
                return [{"course_id": params["course_id"], "chapter_id": params["chapter_id"]}]
            return []

        def execute_write(self, sql, params=None):
            return []

    monkeypatch.setattr(learning_api, "get_mysql_client", FakeMySQL)
    result = client.post("/api/learning/learning/progress", json={
        "user_id": "victim_B", "course_id": "c1", "chapter_id": "1", "progress": 100, "completed": True,
    })
    assert result.status_code == 500
    assert result.get_json()["success"] is False
