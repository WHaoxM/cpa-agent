"""The legacy resume parsing surface must not invent a second student identity."""

from io import BytesIO
from types import SimpleNamespace

from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config


def _client(monkeypatch, *, student_id=None):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()
    if student_id:
        @app.before_request
        def principal():
            g.cpa_principal = Principal("test-principal", student_id, "test")
    return app.test_client()


def test_resume_upload_requires_principal_before_parser(monkeypatch):
    response = _client(monkeypatch).post(
        "/api/pipeline/ext/parse/upload/resume",
        data={"file": (BytesIO(b"resume"), "resume.txt")},
    )
    assert response.status_code == 401


def test_resume_upload_returns_server_student_not_random_id(monkeypatch):
    from app.pipeline.entity_extractor import l0_full_parser

    monkeypatch.setattr(
        l0_full_parser.L0FullParser,
        "parse_file",
        lambda self, _path: SimpleNamespace(
            success=True, text="Education\nProjects", blocks=[], metadata={}, engine="text", error=None,
        ),
    )
    response = _client(monkeypatch, student_id="owner_a").post(
        "/api/pipeline/ext/parse/upload/resume",
        data={"file": (BytesIO(b"resume"), "resume.txt"), "student_id": "other_b"},
    )
    assert response.status_code == 200
    assert response.json["data"]["student_id"] == "owner_a"
    assert response.json["data"]["text"] == "Education\nProjects"


def test_parse_text_writes_only_principal_student_and_fails_on_db_error(monkeypatch):
    class MysqlStub:
        def __init__(self):
            self.params = []
            self.fail = False

        def execute_write(self, _sql, params):
            if self.fail:
                raise RuntimeError("db unavailable")
            self.params.append(params)

    mysql = MysqlStub()
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: mysql)
    client = _client(monkeypatch, student_id="owner_a")
    body = {"text": "A sufficiently long resume with projects and education", "student_id": "other_b"}
    response = client.post("/api/pipeline/ext/parse/text", json=body)
    assert response.status_code == 200
    assert response.json["data"]["student_id"] == "owner_a"
    assert mysql.params[-1]["student_id"] == "owner_a"

    mysql.fail = True
    failed = client.post("/api/pipeline/ext/parse/text", json=body)
    assert failed.status_code == 500
    assert failed.json["success"] is False


def test_nsle_resume_requires_principal_before_model(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.entity_extractor.nsle_pipeline.run_nsle_resume",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model must not run")),
    )
    response = _client(monkeypatch).post(
        "/api/pipeline/ext/nsle/resume", json={"text": "private resume"},
    )
    assert response.status_code == 401


def test_parse_full_failed_child_is_not_reported_complete(monkeypatch):
    from app.pipeline.entity_extractor import l0_parser

    monkeypatch.setattr(
        l0_parser,
        "parse_resume_text",
        lambda _text: SimpleNamespace(success=False, blocks=[], metadata={}, error="invalid"),
    )
    response = _client(monkeypatch, student_id="owner_a").post(
        "/api/pipeline/ext/parse/full",
        json={"student_text": "invalid resume", "student_id": "other_b"},
    )
    assert response.status_code == 200
    assert response.json["success"] is False
    assert response.json["status"] == "failed"
    assert response.json["data"]["resume"]["success"] is False


def test_nsle_full_failed_child_is_not_reported_complete(monkeypatch):
    from app.pipeline.entity_extractor import nsle_pipeline

    monkeypatch.setattr(nsle_pipeline, "run_nsle_resume", lambda _text: {"success": False, "blocks": []})
    response = _client(monkeypatch, student_id="owner_a").post(
        "/api/pipeline/ext/nsle/full",
        json={"resume_text": "invalid resume"},
    )
    assert response.status_code == 200
    assert response.json["success"] is False
    assert response.json["status"] == "failed"
