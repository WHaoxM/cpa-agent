from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config


def _client(monkeypatch, *, admin=False):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def test_auth():
        g.cpa_principal = Principal("operator" if admin else "user_a", "alice", "test_auth")
        if admin:
            g.cpa_admin = True

    return app.test_client()


def test_admin_mutations_require_server_claim_and_never_reach_handler(monkeypatch):
    from app.api import admin as admin_api

    def unreachable():
        raise AssertionError("admin handler must not access DB")

    monkeypatch.setattr(admin_api, "get_mysql_client", unreachable)
    client = _client(monkeypatch)
    for path, body in (
        ("/api/admin/knowledge-base/upload", {"title": "poison"}),
        ("/api/admin/pipeline/build", {"source_id": "dataset_1"}),
        ("/api/admin/graph/import-ability-course", {"roles": [{"role": "X"}]}),
    ):
        res = client.post(path, json=body)
        assert res.status_code == 403
        assert res.json["success"] is False


def test_admin_without_principal_is_unauthorized(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    client = create_app().test_client()
    res = client.post("/api/admin/graph/import-ability-course", json={"roles": []})
    assert res.status_code == 401
    assert res.json["success"] is False


def test_admin_task_not_created_without_authoritative_readback(monkeypatch):
    from app.api import admin as admin_api

    class FakeMysql:
        def execute_write(self, sql, params):
            assert params["triggered_by"] == "operator"

        def fetch_all(self, sql, params):
            return []

    monkeypatch.setattr(admin_api, "get_mysql_client", lambda: FakeMysql())
    client = _client(monkeypatch, admin=True)
    res = client.post("/api/admin/pipeline/build", json={
        "source_id": "dataset_1", "triggered_by": "browser_admin",
    })
    assert res.status_code == 500
    assert res.json["success"] is False


def test_private_vector_search_and_writes_fail_closed(monkeypatch):
    from app.api import pipeline_ext_vector as vector_api

    def unreachable():
        raise AssertionError("Milvus must not be called for private vectors")

    monkeypatch.setattr("app.services.db.get_milvus_client", unreachable)
    client = _client(monkeypatch)
    for collection in ("resume", "conversation", "resume_vectors", "all_private"):
        res = client.post("/api/pipeline/ext/vector/search", json={"query": "private", "collection": collection})
        assert res.status_code == 403
        assert res.json["success"] is False
    res = client.post("/api/pipeline/ext/vector/store/resume", json={
        "student_id": "bob", "text_chunks": ["secret"],
    })
    assert res.status_code == 403
    res = client.post("/api/pipeline/ext/vector/store/resume", json={
        "student_id": "alice", "text_chunks": ["secret"],
    })
    assert res.status_code == 501
    assert res.json["success"] is False
    for path, body in (
        ("/api/pipeline/ext/vector/store/skills", {"skills": [{"name": "poison"}]}),
        ("/api/pipeline/ext/vector/store/jd", {"text_chunks": ["poison"]}),
        ("/api/pipeline/ext/vector/init", {}),
        ("/api/pipeline/ext/vector/rebuild", {}),
    ):
        res = client.post(path, json=body)
        assert res.status_code == 403
        assert res.json["success"] is False


def test_public_vector_all_never_queries_private_collection(monkeypatch):
    from app.api import pipeline_ext_vector as vector_api

    names = []

    class FakeMilvus:
        def connect(self):
            return True

        def search(self, *, collection_name, **kwargs):
            names.append(collection_name)
            return []

    monkeypatch.setattr("app.services.db.get_milvus_client", lambda: FakeMilvus())
    monkeypatch.setattr(vector_api, "_generate_embeddings", lambda _: ([[0.0]], False))
    client = _client(monkeypatch)
    res = client.post("/api/pipeline/ext/vector/search", json={"query": "Python", "collection": "all"})
    assert res.status_code == 200
    assert names == ["skill_vectors", "jd_vectors"]
