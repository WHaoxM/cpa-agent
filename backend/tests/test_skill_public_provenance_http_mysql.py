"""Opt-in MySQL taxonomy -> isolated Lite -> real HTTP provenance boundary."""

import hashlib
import json
import os
import threading
import uuid

import pytest
import requests
from flask import g
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1"
    or os.environ.get("CPA_TEST_LIVE_MILVUS_LITE") != "1",
    reason="requires real MySQL taxonomy and isolated Milvus Lite",
)


class DeterministicTestEmbedding:
    model_name = "test/deterministic-sha256"

    def encode(self, texts, batch_size=16):
        return [[(hashlib.sha256(text.encode("utf-8")).digest()[i % 32] - 127) / 128
                 for i in range(1024)] for text in texts]


def test_admin_sync_cannot_append_browser_text_and_public_search_filters_unbound_hits(
    monkeypatch, tmp_path,
):
    from app import create_app
    from app.api import pipeline_ext_vector
    from app.api._helpers import Principal
    from app.config import Config
    from app.services import db, embedding_service
    from app.services.db.milvus_client import MilvusClient
    from app.services.skill_vector_index import load_taxonomy, reconcile_skill_vectors

    mysql = db.get_mysql_client()
    embedding = DeterministicTestEmbedding()
    desired = load_taxonomy(mysql, embedding.model_name)
    assert len(desired) == 99
    python_item = next(item for item in desired if item["metadata"]["skill_name"] == "Python")
    query_vector = embedding.encode([python_item["text"]])[0]
    isolated = MilvusClient(uri=str(tmp_path / "public-skill-provenance.db"))
    try:
        first = reconcile_skill_vectors(mysql, isolated, embedding,
                                        expected_source_count=len(desired))
        assert first["verified"] == len(desired) and first["unmanaged"] == 0
        monkeypatch.setattr(db, "get_milvus_client", lambda: isolated)
        monkeypatch.setattr(db, "get_mysql_client", lambda: mysql)
        monkeypatch.setattr(embedding_service, "get_embedding_service", lambda: embedding)
        monkeypatch.setattr(pipeline_ext_vector, "_generate_embeddings",
                            lambda _texts: ([query_vector], False))
        monkeypatch.setattr(Config, "EMBEDDING_MODEL", embedding.model_name)
        nonce = uuid.uuid4().hex
        config = type("SkillProvenanceConfig", (Config,), {
            "SECRET_KEY": "skill-provenance-test-secret-1234567890123456789",
            "DEMO_MODE": False, "RATELIMIT_KEY_PREFIX": f"skill-provenance-{nonce}",
        })
        app = create_app(config)

        @app.before_request
        def admin_claim():
            g.cpa_principal = Principal("skill-provenance-admin", "skill-provenance-admin", "test")
            g.cpa_admin = True

        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/pipeline/ext/vector"
        session = requests.Session()
        session.trust_env = False
        try:
            legacy = session.post(base + "/store/skills", json={
                "skills": [{"name": "Fake", "description": "UNTRUSTED public text"}]}, timeout=10)
            assert legacy.status_code == 400
            synced = session.post(base + "/store/skills", json={
                "source": "mysql.skill_taxonomy", "expected_source_count": len(desired)}, timeout=20)
            assert synced.status_code == 200, synced.text
            assert synced.json()["data"]["inserted"] == 0
            assert synced.json()["data"]["verified"] == len(desired)
            wrong_count = session.post(base + "/store/skills", json={
                "source": "mysql.skill_taxonomy", "expected_source_count": len(desired) - 1}, timeout=10)
            assert wrong_count.status_code == 409

            # Simulate a historical unmanaged index entry nearest to the query.
            isolated.insert("skill_vectors", ["UNTRUSTED public text"], [query_vector],
                            [{"skill_name": "Fake"}])
            searched = session.post(base + "/search", json={
                "query": "Python", "collection": "skill", "top_k": 3}, timeout=20)
            assert searched.status_code == 200, searched.text
            data = searched.json()["data"]
            assert data["status"] == "partial"
            assert data["rejected_unverified_hits"]["skill_vectors"] >= 1
            hits = data["results"]["skill_vectors"]
            assert hits
            assert json.loads(hits[0]["metadata"])["skill_name"] == "Python"
            assert all("UNTRUSTED" not in hit["text"] for hit in hits)

            refused = session.post(base + "/store/skills", json={
                "source": "mysql.skill_taxonomy", "expected_source_count": len(desired)}, timeout=20)
            assert refused.status_code == 409  # Existing unmanaged row is not silently adopted.
        finally:
            session.close()
            server.shutdown()
            thread.join(timeout=10)
    finally:
        if isolated._client is not None:
            isolated._client.close()
        isolated.disconnect()
