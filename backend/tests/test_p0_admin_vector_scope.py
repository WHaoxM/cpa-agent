import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from flask import g
from werkzeug.serving import make_server

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
    diagnostic = client.get("/api/admin/python-info")
    assert diagnostic.status_code == 401
    assert diagnostic.json["success"] is False


def test_python_diagnostic_requires_server_admin_claim(monkeypatch):
    ordinary = _client(monkeypatch)
    denied = ordinary.get("/api/admin/python-info")
    assert denied.status_code == 403
    assert denied.json["success"] is False

    admin = _client(monkeypatch, admin=True)
    allowed = admin.get("/api/admin/python-info")
    assert allowed.status_code == 200
    assert allowed.json["python_executable"]


def test_python_diagnostic_rejects_public_socket_requests(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setenv("CPA_DEV_AUTH", "1")
    app = create_app()
    server = make_server("127.0.0.1", 0, app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/admin/python-info"
        for headers, expected in (
            ({}, 401),
            ({"X-Dev-Student-Id": "secondary_acceptance"}, 403),
        ):
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url, headers=headers), timeout=5)
            assert error.value.code == expected
    finally:
        server.shutdown()
        worker.join(timeout=5)


def test_admin_task_not_created_without_dispatcher(monkeypatch):
    from app.api import admin as admin_api

    def unreachable():
        raise AssertionError("Unconsumable build must not access DB")

    monkeypatch.setattr(admin_api, "get_mysql_client", unreachable)
    client = _client(monkeypatch, admin=True)
    res = client.post("/api/admin/pipeline/build", json={
        "source_id": "dataset_1", "triggered_by": "browser_admin",
    })
    assert res.status_code == 503
    assert res.json["success"] is False
    assert res.json["code"] == "admin_pipeline_build_unavailable"


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


def test_jd_vector_write_rejects_client_text_and_indexes_current_publication(monkeypatch):
    """JD vectors are derived from the current trusted snapshot, never browser text."""
    from app.services import job_publication
    from app.services import db

    inserted = []

    class FakeMilvus:
        def connect(self):
            return True

        def insert(self, **kwargs):
            inserted.append(kwargs)
            return ["vector-1"]

    snapshot = {"source_text": "Official source JD: Python and accounting.",
                "position_name": "Audited backend engineer"}
    published = {"id": 77, "job_portrait_id": "job_verified_1",
                 "publication_version": "v-current", "content_sha256": "a" * 64,
                 "snapshot_json": snapshot}
    calls = []

    def read(mysql, job_id, version=None):
        calls.append((job_id, version))
        return published if job_id == "job_verified_1" and version in (None, "v-current") else None

    monkeypatch.setattr(job_publication, "read_published_job", read)
    monkeypatch.setattr(db, "get_mysql_client", lambda: object())
    monkeypatch.setattr(db, "get_milvus_client", lambda: FakeMilvus())
    monkeypatch.setattr("app.api.pipeline_ext_vector._generate_embeddings",
                        lambda texts: ([[0.1, 0.2] for _ in texts], False))
    client = _client(monkeypatch, admin=True)

    # Replays the old vulnerable payload shape: arbitrary client text must be
    # rejected before MySQL lookup, embedding generation, or Milvus writes.
    old_payload = {"job_id": "job_verified_1", "position_name": "fake title",
                   "text_chunks": ["CLIENT CONTROLLED FAKE JD"]}
    rejected = client.post("/api/pipeline/ext/vector/store/jd", json=old_payload)
    assert rejected.status_code == 400
    assert not calls and not inserted

    # Exact current publication is looked up twice (requested version + latest)
    # and only the immutable server snapshot is sent to embedding/Milvus.
    accepted = client.post("/api/pipeline/ext/vector/store/jd", json={
        "job_id": "job_verified_1", "publication_version": "v-current",
        "content_sha256": "a" * 64,
    })
    assert accepted.status_code == 200, accepted.json
    assert calls == [("job_verified_1", "v-current"), ("job_verified_1", None)]
    assert inserted[0]["texts"] == [snapshot["source_text"]]
    assert inserted[0]["metadata"] == [{
        "job_portrait_id": "job_verified_1",
        "position_name": "Audited backend engineer",
        "publication_version": "v-current",
        "content_sha256": "a" * 64,
    }]


def test_jd_vector_write_rejects_stale_or_unpublished_version_before_milvus(monkeypatch):
    from app.services import db, job_publication

    def read(mysql, job_id, version=None):
        if version == "v-old":
            return {"id": 1, "publication_version": "v-old", "content_sha256": "b" * 64,
                    "snapshot_json": {"source_text": "old", "position_name": "Old"}}
        if version is None:
            return {"id": 2, "publication_version": "v-new", "content_sha256": "c" * 64,
                    "snapshot_json": {"source_text": "new", "position_name": "New"}}
        return None

    def unreachable():
        raise AssertionError("stale/unpublished JD must not reach Milvus")

    monkeypatch.setattr(job_publication, "read_published_job", read)
    monkeypatch.setattr(db, "get_mysql_client", lambda: object())
    monkeypatch.setattr(db, "get_milvus_client", unreachable)
    client = _client(monkeypatch, admin=True)
    response = client.post("/api/pipeline/ext/vector/store/jd", json={
        "job_id": "job_verified_1", "publication_version": "v-old",
        "content_sha256": "b" * 64,
    })
    assert response.status_code == 409
    assert response.json["success"] is False


@pytest.mark.parametrize("body, expected", [
    (["Python"], 400), (42, 400), ({}, 400),
    ({"query": []}, 400), ({"query": " "}, 400),
    ({"query": "Python", "collection": []}, 403),
    ({"query": "Python", "top_k": "5"}, 400),
    ({"query": "Python", "top_k": True}, 400),
])
def test_public_vector_input_rejected_before_index_access(monkeypatch, body, expected):
    def unreachable():
        raise AssertionError("invalid input reached Milvus")

    monkeypatch.setattr("app.services.db.get_milvus_client", unreachable)
    client = _client(monkeypatch)
    response = client.post("/api/pipeline/ext/vector/search", json=body)
    assert response.status_code == expected
    assert response.json["success"] is False
