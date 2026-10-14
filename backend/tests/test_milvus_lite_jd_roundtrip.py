"""Opt-in live Milvus Lite round-trip for the JD vector write/read adapter."""

import json
import os
import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from flask import g


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MILVUS_LITE") != "1",
    reason="requires local Milvus Lite runtime and creates a temporary vector DB",
)


def test_one_jd_vector_can_be_inserted_and_searched(tmp_path):
    from app.services.db.milvus_client import MilvusClient

    milvus = MilvusClient(uri=str(tmp_path / "jd-probe.db"))
    metadata = {"job_portrait_id": "probe_publication",
                "publication_version": "v1", "content_sha256": "a" * 64}
    vector = [0.0] * 1024
    vector[0] = 1.0
    try:
        assert milvus.connect()
        ids = milvus.insert("jd_vectors", ["Official synthetic JD"], [vector], [metadata])
        assert len(ids) == 1
        hits = milvus.search("jd_vectors", vector, top_k=1, strict=True)
        assert len(hits) == 1
        assert hits[0]["text"] == "Official synthetic JD"
        assert json.loads(hits[0]["metadata"]) == metadata
    finally:
        if milvus._client is not None:
            milvus._client.close()
        milvus.disconnect()


def test_admin_skill_write_and_public_search_use_real_milvus_lite(monkeypatch, tmp_path):
    from app import create_app
    from app.api._helpers import Principal
    from app.api import pipeline_ext_vector
    from app.config import Config
    from app.services import db, embedding_service
    from app.services.db.milvus_client import MilvusClient
    from app.services.skill_vector_index import READ_SQL, _canonical

    milvus = MilvusClient(uri=str(tmp_path / "skill-routes.db"))
    monkeypatch.setattr(db, "get_milvus_client", lambda: milvus)
    row = {"id": 1, "skill_name": "Python", "category": "Programming",
           "sub_category": "Language", "tier": 1, "dimension": "D2"}

    class FakeMysql:
        def execute(self, sql):
            assert sql == READ_SQL
            return [row]

    class FakeEmbedding:
        model_name = "test/skill-route"

        def encode(self, texts, batch_size=16):
            return [vector for _ in texts]

    monkeypatch.setattr(db, "get_mysql_client", lambda: FakeMysql())
    monkeypatch.setattr(embedding_service, "get_embedding_service", lambda: FakeEmbedding())
    monkeypatch.setattr(Config, "EMBEDDING_MODEL", FakeEmbedding.model_name)
    vector = [0.0] * 1024
    vector[0] = 1.0
    monkeypatch.setattr(pipeline_ext_vector, "_generate_embeddings",
                        lambda texts: ([vector for _ in texts], False))
    config = type("LiveSkillConfig", (Config,), {
        "SECRET_KEY": "live-skill-test-secret-with-32-bytes", "DEMO_MODE": False,
    })
    app = create_app(config)

    @app.before_request
    def test_admin():
        g.cpa_principal = Principal("test-operator", "test", "fixture")
        g.cpa_admin = True

    try:
        client = app.test_client()
        written = client.post("/api/pipeline/ext/vector/store/skills", json={
            "source": "mysql.skill_taxonomy", "expected_source_count": 1,
        })
        assert written.status_code == 200, written.get_json()
        assert written.get_json()["data"]["verified"] == 1

        searched = client.post("/api/pipeline/ext/vector/search", json={
            "query": "Python", "collection": "skill",
        })
        assert searched.status_code == 200, searched.get_json()
        hits = searched.get_json()["data"]["results"]["skill_vectors"]
        assert len(hits) == 1
        assert hits[0]["text"] == _canonical(row, FakeEmbedding.model_name)["text"]
        assert json.loads(hits[0]["metadata"]) == _canonical(
            row, FakeEmbedding.model_name)["metadata"]
    finally:
        if milvus._client is not None:
            milvus._client.close()
        milvus.disconnect()


@pytest.mark.skipif(os.environ.get("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="requires Docker MySQL and verified publication table")
def test_current_publication_routes_through_real_milvus_lite(monkeypatch, tmp_path):
    from app import create_app
    from app.api._helpers import Principal
    from app.api import pipeline_ext_vector
    from app.config import Config
    from app.services import db
    from app.services.db.milvus_client import MilvusClient
    from app.services.job_publication import content_sha256, publish_job

    nonce = uuid.uuid4().hex[:12]
    job_id = f"lite_jd_{nonce}"
    source = "Synthetic Role. Bachelor required. Python required. No major restriction. No certificates."
    snapshot = {
        "position_name": "Synthetic Role", "source_uri": "https://example.test/lite-probe",
        "source_text": source,
        "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_fetched_at": "2026-01-01T00:00:00Z",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": "test-operator", "source_license": "test-only",
        "review_state": "approved", "required_skills": [{"name": "Python"}],
        "job_profile": {"degree_requirement": "本科", "major_requirements": [],
                        "major_strict": False, "required_certs": []},
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": "Synthetic Role"},
            "degree_requirement": {"status": "explicit", "excerpt": "Bachelor required"},
            "major_requirements": {"status": "explicit", "excerpt": "No major restriction"},
            "major_strict": {"status": "explicit", "excerpt": "No major restriction"},
            "required_certs": {"status": "explicit", "excerpt": "No certificates"},
            "required_skills": {"status": "explicit", "excerpt": "Python required"},
        },
    }
    mysql = db.get_mysql_client()
    milvus = MilvusClient(uri=str(tmp_path / "routes.db"))
    monkeypatch.setattr(db, "get_milvus_client", lambda: milvus)
    vector = [0.0] * 1024
    vector[0] = 1.0
    monkeypatch.setattr(pipeline_ext_vector, "_generate_embeddings",
                        lambda texts: ([vector for _ in texts], False))
    config = type("LiveVectorConfig", (Config,), {
        "SECRET_KEY": "live-vector-test-secret-with-32-bytes",
        "DEMO_MODE": False, "RATELIMIT_KEY_PREFIX": f"live-vector-{nonce}",
    })
    app = create_app(config)

    @app.before_request
    def test_admin():
        g.cpa_principal = Principal("test-operator", "test", "fixture")
        g.cpa_admin = True

    try:
        publish_job(mysql, job_portrait_id=job_id, version="v1", snapshot=snapshot)
        client = app.test_client()
        written = client.post("/api/pipeline/ext/vector/store/jd", json={
            "job_id": job_id, "publication_version": "v1",
            "content_sha256": content_sha256(snapshot)})
        assert written.status_code == 200, written.get_json()
        assert written.get_json()["data"]["count"] == 1

        searched = client.post("/api/pipeline/ext/vector/search", json={
            "query": "Python", "collection": "jd"})
        assert searched.status_code == 200, searched.get_json()
        hits = searched.get_json()["data"]["results"]["jd_vectors"]
        assert len(hits) == 1
        assert hits[0]["job_portrait_id"] == job_id
        assert hits[0]["publication_version"] == "v1"
        assert hits[0]["content_sha256"] == content_sha256(snapshot)
        assert "text" not in hits[0] and "metadata" not in hits[0]

        mysql.execute_write(
            "INSERT INTO job_catalog_publication "
            "(job_portrait_id,publication_version,snapshot_json,content_sha256,status,published_at) "
            "VALUES (:id,'v2',:snapshot,:digest,'revoked',NOW())",
            {"id": job_id, "snapshot": json.dumps(snapshot, ensure_ascii=False),
             "digest": content_sha256(snapshot)})
        after = client.post("/api/pipeline/ext/vector/search", json={
            "query": "Python", "collection": "jd"})
        assert after.status_code == 200, after.get_json()
        assert not after.get_json()["data"]["results"].get("jd_vectors")
    finally:
        mysql.execute_write("DELETE FROM job_catalog_publication WHERE job_portrait_id=:id",
                            {"id": job_id})
        if milvus._client is not None:
            milvus._client.close()
        milvus.disconnect()
        assert mysql.execute_single(
            "SELECT COUNT(*) AS n FROM job_catalog_publication WHERE job_portrait_id=:id",
            {"id": job_id})["n"] == 0
