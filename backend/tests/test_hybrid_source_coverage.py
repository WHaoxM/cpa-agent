"""A usable candidate must not conceal an unavailable retrieval source."""

import uuid

from app import create_app
from app.config import Config
from app.services import hybrid_retrieve


class RoleCatalog:
    def execute_single(self, sql, params=None):
        if params and params.get("name") == "Public Role":
            return {"role_name": "Public Role"}
        return None


def _client(monkeypatch):
    from app.services import db

    monkeypatch.setattr(db, "get_mysql_client", lambda: RoleCatalog())
    monkeypatch.setattr(hybrid_retrieve, "keyword_retrieve_jobs", lambda *args, **kwargs: [
        {"id": "role:Public Role", "role_name": "Public Role",
         "channel": "keyword", "matched_keywords": ["Python"]}
    ])
    config = type("HybridCoverageConfig", (Config,), {
        "SECRET_KEY": "hybrid-coverage-test-only-secret-with-32-bytes",
        "DEMO_MODE": False,
        "RATELIMIT_KEY_PREFIX": f"hybrid-coverage-{uuid.uuid4().hex}",
    })
    return create_app(config).test_client()


def test_vector_outage_retains_candidate_but_marks_partial(monkeypatch):
    client = _client(monkeypatch)

    def unavailable(*args, **kwargs):
        raise hybrid_retrieve.VectorRetrievalUnavailable("fixture vector outage")

    monkeypatch.setattr(hybrid_retrieve, "vector_retrieve_jobs", unavailable)
    response = client.post("/api/match/hybrid-candidates", json={
        "skills": ["Python"], "enable_graph_hop": False})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "partial"
    assert data["failed_sources"] == ["vector"]
    assert data["source_coverage"]["vector"] == "unavailable"
    assert data["candidates"][0]["id"] == "role:Public Role"
    assert data["candidates"][0]["match_eligible"] is False


def test_graph_outage_marks_partial_without_dropping_candidate(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(hybrid_retrieve, "vector_retrieve_jobs", lambda *args, **kwargs: [])
    monkeypatch.setattr(hybrid_retrieve, "graph_hop_for_skills", lambda *args, **kwargs: {
        "role": "Public Role", "resolved_role": "Public Role",
        "paths": [], "related_courses": [], "status": "unavailable"})
    response = client.post("/api/match/hybrid-candidates", json={"skills": ["Python"]})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "partial"
    assert data["failed_sources"] == ["graph"]
    assert data["candidates"][0]["id"] == "role:Public Role"


def test_empty_is_distinct_from_failed_source(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(hybrid_retrieve, "keyword_retrieve_jobs", lambda *args, **kwargs: [])
    monkeypatch.setattr(hybrid_retrieve, "vector_retrieve_jobs", lambda *args, **kwargs: [])
    response = client.post("/api/match/hybrid-candidates", json={
        "skills": ["Unknown"], "enable_graph_hop": False})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "empty"
    assert data["failed_sources"] == []
    assert data["candidates"] == []


def test_real_vector_function_reports_model_outage(monkeypatch):
    from app.services import embedding_service

    class UnavailableEmbedding:
        def is_available(self):
            return False

    monkeypatch.setattr(embedding_service, "get_embedding_service",
                        lambda: UnavailableEmbedding())
    try:
        hybrid_retrieve.vector_retrieve_jobs(["Python"])
    except hybrid_retrieve.VectorRetrievalUnavailable:
        pass
    else:
        raise AssertionError("model outage was mistaken for no matching vectors")


def test_graph_resolution_failure_is_explicitly_unavailable(monkeypatch):
    from app.services import ability_graph_service

    def fail(role):
        raise RuntimeError("fixture Neo4j unavailable")

    monkeypatch.setattr(ability_graph_service, "resolve_ability_role", fail)
    result = hybrid_retrieve.graph_hop_for_skills("Public Role", ["Python"])
    assert result["status"] == "unavailable"
    assert result["paths"] == []


def test_milvus_strict_search_does_not_convert_exception_to_empty():
    from app.services.db.milvus_client import MilvusClient

    class BrokenIndex:
        def has_collection(self, name):
            return True

        def search(self, **kwargs):
            raise RuntimeError("fixture index outage")

    milvus = MilvusClient()
    milvus._connected = True
    milvus._client = BrokenIndex()
    assert milvus.search("jd_vectors", [0.1], strict=False) == []
    try:
        milvus.search("jd_vectors", [0.1], strict=True)
    except RuntimeError as exc:
        assert "fixture index outage" in str(exc)
    else:
        raise AssertionError("strict search hid index outage")
