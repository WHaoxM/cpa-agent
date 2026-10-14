from flask import g

from app import create_app
from app.api import pipeline_ext_agent as agent_api
from app.api import pipeline_ext_report as report_api
from app.api import pipeline_ext_assessment as assessment_api
from app.api import graph as graph_api
from app.config import Config


def _client(monkeypatch, *, principal=True):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()
    if principal:
        @app.before_request
        def bind_principal():
            g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}
    return app.test_client()


def test_ext_resource_routes_fail_closed_without_principal(monkeypatch):
    client = _client(monkeypatch, principal=False)
    assert client.get("/api/pipeline/ext/report/list").status_code == 401
    assert client.get("/api/pipeline/ext/report/r1/status").status_code == 401
    assert client.get("/api/pipeline/ext/exams/a1").status_code == 401
    assert client.post("/api/pipeline/ext/exams", json={"answers":[{"skill":"Python","level":2}]}).status_code == 401
    assert client.get("/api/pipeline/ext/agent/stats").status_code == 401
    assert client.get("/api/pipeline/ext/agent/session/s1/messages").status_code == 401
    assert client.post("/api/pipeline/ext/agent/session/save", json={"session_id":"s1","messages":[{"role":"user","content":"x"}]}).status_code == 401


def test_ext_report_and_exam_reads_are_owner_scoped(monkeypatch):
    client = _client(monkeypatch)
    calls = []

    class FakeMySQL:
        def fetch_all(self, sql, params):
            calls.append((sql, params))
            return []

        def execute_single(self, sql, params):
            calls.append((sql, params))
            return None

        def execute(self, sql, params):
            calls.append((sql, params))
            return []

    monkeypatch.setattr("app.services.db.get_mysql_client", FakeMySQL)
    assert client.get("/api/pipeline/ext/report/list?student_id=victim_B").status_code == 200
    assert client.get("/api/pipeline/ext/report/r_victim/status").status_code == 404
    assert client.get("/api/pipeline/ext/exams/a_victim").status_code == 404
    assert all("student_id" in sql for sql, _ in calls)
    assert all(params["student_id"] == "owner_A" for _, params in calls)


def test_ext_exam_post_uses_principal_and_no_fake_id_without_readback(monkeypatch):
    client = _client(monkeypatch)
    writes = []

    class FakeMySQL:
        def execute_write(self, sql, params):
            writes.append(params)

        def execute(self, sql, params):
            return []

    monkeypatch.setattr("app.services.db.get_mysql_client", FakeMySQL)
    result = client.post("/api/pipeline/ext/exams", json={
        "student_id":"victim_B", "answers":[{"skill":"Python","level":2}],
    })
    assert writes[0]["student_id"] == "owner_A"
    assert result.status_code == 500
    assert result.get_json()["success"] is False


def test_ext_exam_rejects_malformed_answers_and_levels_before_db(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        "app.services.db.get_mysql_client",
        lambda: (_ for _ in ()).throw(AssertionError("DB accessed")),
    )
    invalid = [
        ["not-an-answer"],
        [{"skill": "Python", "level": "2"}],
        [{"skill": "Python", "level": True}],
        [{"skill": "Python", "level": -1}],
        [{"skill": "Python", "level": 6}],
        [{"skill": "  ", "level": 2}],
    ]
    for answers in invalid:
        response = client.post("/api/pipeline/ext/exams", json={"answers": answers})
        assert response.status_code == 400
        assert response.get_json()["success"] is False


def test_ext_exam_saved_record_does_not_claim_fabricated_portrait_or_roles(monkeypatch):
    client = _client(monkeypatch)

    class FakeMySQL:
        def execute_write(self, sql, params):
            return []

        def execute(self, sql, params):
            if "SELECT assessment_id" in sql:
                return [{"assessment_id": params["assessment_id"]}]
            return []

    monkeypatch.setattr("app.services.db.get_mysql_client", FakeMySQL)
    result = client.post("/api/pipeline/ext/exams", json={
        "answers":[{"skill":"Unmatched skill","level":1}],
    })
    data = result.get_json()["data"]
    assert result.status_code == 200
    assert data["status"] == "partial"
    assert data["portrait"] is None
    assert data["portrait_ready"] is False
    assert data["recommended_roles"] == []


def test_ext_agent_rejects_foreign_session_before_writing_messages(monkeypatch):
    client = _client(monkeypatch)
    writes = []

    class FakeMySQL:
        def execute_write(self, sql, params):
            writes.append((sql, params))

        def execute(self, sql, params):
            return [{"student_id": "victim_B"}]

    monkeypatch.setattr("app.services.db.get_mysql_client", FakeMySQL)
    result = client.post("/api/pipeline/ext/agent/session/save", json={
        "session_id":"s_victim", "student_id":"victim_B",
        "messages":[{"role":"user","content":"inject"}],
    })
    assert result.status_code == 403
    assert writes[0][1]["student_id"] == "owner_A"
    assert not any("conversation_message" in sql for sql, _ in writes)


def test_ext_agent_stats_and_messages_reject_foreign_session(monkeypatch):
    client = _client(monkeypatch)

    class FakeMySQL:
        def execute(self, sql, params):
            return [{"student_id": "victim_B"}]

        def fetch_all(self, sql, params):
            raise AssertionError("foreign messages or stats accessed")

    monkeypatch.setattr("app.services.db.get_mysql_client", FakeMySQL)
    assert client.get("/api/pipeline/ext/agent/stats?session_id=s_victim").status_code == 403
    assert client.get("/api/pipeline/ext/agent/session/s_victim/messages").status_code == 403


def test_graph_build_uses_persisted_task_id_and_state(monkeypatch):
    client = _client(monkeypatch)
    from app.services import pipeline as pipeline_service

    class FakeMySQL:
        def execute(self, sql, params):
            assert params == {"task_id":"task_real", "student_id":"owner_A"}
            return [{"task_id":"task_real", "status":"partial"}]

    class FakeRunner:
        mysql = FakeMySQL()

        def run(self, pipeline, params):
            assert params["student_id"] == "owner_A"
            return {"task_id":"task_real", "student_id":"owner_A"}

    monkeypatch.setattr(pipeline_service, "PipelineRunner", FakeRunner)
    result = client.post("/api/graph/build", json={"type":"student_skills","student_id":"victim_B","profile_text":"Python"})
    assert result.status_code == 200
    assert result.get_json()["success"] is False
    assert result.get_json()["data"]["task_id"] == "task_real"
    assert result.get_json()["data"]["status"] == "partial"
