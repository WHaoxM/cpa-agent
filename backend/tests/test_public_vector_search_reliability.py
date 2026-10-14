"""Public vector search distinguishes a working empty index from a failed one."""

import uuid

from app import create_app
from app.config import Config


def _client(monkeypatch, milvus):
    from app.api import pipeline_ext_vector
    from app.services import db
    from app.services import skill_vector_index

    monkeypatch.setattr(db, "get_milvus_client", lambda: milvus)
    monkeypatch.setattr(pipeline_ext_vector, "_generate_embeddings",
                        lambda texts: ([[0.1, 0.2]], False))
    # These tests exercise transport/error semantics; provenance has its own
    # real MySQL + Milvus Lite acceptance tests.
    monkeypatch.setattr(skill_vector_index, "project_public_skill_vector_hits",
                        lambda _mysql, hits, _model: (hits, 0))
    config = type("VectorReliabilityConfig", (Config,), {
        "SECRET_KEY": "vector-reliability-test-secret-with-32-bytes",
        "DEMO_MODE": False,
        "RATELIMIT_KEY_PREFIX": f"vector-reliability-{uuid.uuid4().hex}",
    })
    return create_app(config).test_client()


def test_public_vector_query_uses_one_flat_embedding(monkeypatch):
    class Milvus:
        def connect(self):
            return True

        def search(self, *, collection_name, query_vector, **kwargs):
            assert collection_name == "skill_vectors"
            assert query_vector == [0.1, 0.2]
            return [{"id": "public-skill", "text": "Python", "metadata": "{}"}]

    client = _client(monkeypatch, Milvus())
    response = client.post("/api/pipeline/ext/vector/search", json={
        "query": "Python", "collection": "skill"})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "complete"
    assert len(data["results"]["skill_vectors"]) == 1


def test_failed_only_collection_is_not_reported_as_empty(monkeypatch):
    class Milvus:
        def connect(self):
            return True

        def search(self, **kwargs):
            raise RuntimeError("fixture index failure")

    client = _client(monkeypatch, Milvus())
    response = client.post("/api/pipeline/ext/vector/search", json={
        "query": "Python", "collection": "jd"})
    assert response.status_code == 503
    assert response.get_json()["status"] == "unavailable"


def test_all_preserves_skill_hits_and_marks_jd_failure(monkeypatch):
    class Milvus:
        def connect(self):
            return True

        def search(self, *, collection_name, query_vector, **kwargs):
            assert query_vector == [0.1, 0.2]
            if collection_name == "jd_vectors":
                raise RuntimeError("fixture JD index failure")
            return [{"id": "public-skill", "text": "Python", "metadata": "{}"}]

    client = _client(monkeypatch, Milvus())
    response = client.post("/api/pipeline/ext/vector/search", json={
        "query": "Python", "collection": "all"})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "partial"
    assert data["failed_collections"] == ["jd_vectors"]
    assert len(data["results"]["skill_vectors"]) == 1

    only_jd = client.post("/api/pipeline/ext/vector/search", json={
        "query": "Python", "collection": "jd"})
    assert only_jd.status_code == 503
    assert only_jd.get_json()["status"] == "unavailable"


def test_all_preserves_skill_hits_when_jd_publication_cannot_be_verified(monkeypatch):
    from app.api import pipeline_ext_vector
    from app.services.hybrid_retrieve import CandidateCatalogUnavailable

    class Milvus:
        def connect(self):
            return True

        def search(self, *, collection_name, **kwargs):
            return [{"id": "hit", "text": "raw", "metadata": "{}"}]

    def unavailable(*_args):
        raise CandidateCatalogUnavailable("fixture publication store failure")

    monkeypatch.setattr(pipeline_ext_vector, "project_public_jd_vector_hits", unavailable)
    client = _client(monkeypatch, Milvus())
    response = client.post("/api/pipeline/ext/vector/search", json={
        "query": "Python", "collection": "all"})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "partial"
    assert data["failed_collections"] == ["jd_vectors"]
    assert len(data["results"]["skill_vectors"]) == 1

    only_jd = client.post("/api/pipeline/ext/vector/search", json={
        "query": "Python", "collection": "jd"})
    assert only_jd.status_code == 503
    assert only_jd.get_json()["status"] == "unavailable"
