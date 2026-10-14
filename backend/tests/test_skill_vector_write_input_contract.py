"""Admin skill sync rejects browser-authored vectors and counts the DB source."""

import pytest
from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config


@pytest.fixture
def admin_client(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_admin():
        g.cpa_principal = Principal("skill-index-admin", "operator", "test")
        g.cpa_admin = True

    return app.test_client()


@pytest.mark.parametrize("payload", [
    ["not-an-object"],
    {"skills": "Python"},
    {"skills": [{"name": "Python"}, "bad-row"]},
    {"skills": [{"name": "   "}]},
    {"skills": [{"name": ["Python"]}]},
    {"skills": [{"name": "Python", "description": {"text": "bad"}}]},
    {"skills": [{"name": "Python", "category": ["language"]}]},
    {"skills": [{"name": "x" * 201}]},
    {"skills": [{"name": "Python", "description": "x" * 4001}]},
    {"skills": [{"name": "Python", "description": " " * 4001}]},
    {"skills": [{"name": "Python", "category": " " * 101}]},
    {"skills": [{"name": "Python"}] * 101},
])
def test_invalid_skill_batch_is_rejected_before_milvus(admin_client, monkeypatch, payload):
    from app.services import db

    def no_milvus():
        raise AssertionError("invalid request must not touch Milvus")

    monkeypatch.setattr(db, "get_milvus_client", no_milvus)
    response = admin_client.post("/api/pipeline/ext/vector/store/skills", json=payload)
    assert response.status_code == 400, response.get_json()
    assert response.get_json()["success"] is False


def test_legacy_browser_skill_text_cannot_append_unmanaged_public_vector(admin_client, monkeypatch):
    from app.services import db

    monkeypatch.setattr(db, "get_milvus_client",
                        lambda: pytest.fail("browser text reached Milvus"))
    response = admin_client.post("/api/pipeline/ext/vector/store/skills", json={
        "skills": [{"name": " Python ", "description": " Python language ",
                    "category": " Language "}],
    })
    assert response.status_code == 400, response.get_json()


def test_admin_sync_uses_server_taxonomy_and_expected_count(admin_client, monkeypatch):
    from app.services import db, embedding_service, skill_vector_index

    class FakeEmbedding:
        model_name = "test/model"

    fake_mysql, fake_milvus, fake_embedding = object(), object(), FakeEmbedding()
    monkeypatch.setattr(db, "get_mysql_client", lambda: fake_mysql)
    monkeypatch.setattr(db, "get_milvus_client", lambda: fake_milvus)
    monkeypatch.setattr(embedding_service, "get_embedding_service", lambda: fake_embedding)
    calls = []

    def reconcile(mysql, milvus, embedding, *, expected_source_count):
        calls.append((mysql, milvus, embedding, expected_source_count))
        return {"source_rows": 99, "inserted": 0, "deleted": 0,
                "verified": 99, "unmanaged": 0}

    monkeypatch.setattr(skill_vector_index, "reconcile_skill_vectors", reconcile)
    response = admin_client.post("/api/pipeline/ext/vector/store/skills", json={
        "source": "mysql.skill_taxonomy", "expected_source_count": 99,
    })
    assert response.status_code == 200, response.get_json()
    assert calls == [(fake_mysql, fake_milvus, fake_embedding, 99)]
    assert response.get_json()["data"]["content_reconciled"] is True
