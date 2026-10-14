"""An unreviewed portrait or stale vector hit must not become a public job."""

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone

import pytest
import requests
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires Docker MySQL and real Flask HTTP",
)


def _approved(name):
    source = f"{name}. Bachelor required. Python required. No major restriction. No certificates."
    return {
        "position_name": name, "source_uri": "https://example.test/hybrid-published",
        "source_text": source, "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_fetched_at": "2026-01-01T00:00:00Z",
        "reviewed_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": "test-operator",
        "source_license": "test-only", "review_state": "approved",
        "required_skills": [{"name": "Python"}],
        "job_profile": {"degree_requirement": "本科", "major_requirements": [],
                        "major_strict": False, "required_certs": []},
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": name},
            "degree_requirement": {"status": "explicit", "excerpt": "Bachelor required"},
            "major_requirements": {"status": "explicit", "excerpt": "No major restriction"},
            "major_strict": {"status": "explicit", "excerpt": "No major restriction"},
            "required_certs": {"status": "explicit", "excerpt": "No certificates"},
            "required_skills": {"status": "explicit", "excerpt": "Python required"},
        },
    }


def test_hybrid_only_returns_current_published_job_snapshot(monkeypatch):
    from app import create_app
    from app.config import Config
    from app.services import hybrid_retrieve
    from app.services.db import get_mysql_client
    from app.services.job_publication import content_sha256, publish_job
    from app.api import pipeline_ext_vector

    nonce = uuid.uuid4().hex[:10]
    unpublished_id = f"hybrid_unpub_{nonce}"
    published_id = f"hybrid_pub_{nonce}"
    source_only_id = f"source_csb_{int(nonce[:8], 16)}"
    role_name = f"Hybrid Role QA {nonce}"
    published_name = f"Verified Hybrid QA {nonce}"
    db = get_mysql_client()
    config = type("HybridCatalogConfig", (Config,), {
        "SECRET_KEY": "hybrid-catalog-test-only-secret-with-32-bytes",
        "DEMO_MODE": False, "RATELIMIT_KEY_PREFIX": f"hybrid-gate-{nonce}",
    })
    app = create_app(config)
    db.execute_write(
        "INSERT INTO job_portrait_4d "
        "(portrait_id,position_name,jd_count,required_degree,required_skill_cnt,portrait_json) "
        "VALUES (:id,:name,1,'本科',1,:json)",
        {"id": unpublished_id, "name": f"UNREVIEWED {nonce}",
         "json": json.dumps({"D2_requirements": {"required_skills": ["Python"]}})},
    )
    snapshot = _approved(published_name)
    publish_job(db, job_portrait_id=published_id, version="v1", snapshot=snapshot)
    db.execute_write(
        "INSERT INTO career_insights (role_name,category,core_skills) "
        "VALUES (:role,'test',:skills)",
        {"role": role_name, "skills": json.dumps(["Python"])},
    )
    monkeypatch.setattr(hybrid_retrieve, "vector_retrieve_jobs", lambda names, top_k: [
        {"id": unpublished_id, "job_portrait_id": unpublished_id,
         "position_name": f"VECTOR UNREVIEWED {nonce}", "text": f"PRIVATE {nonce}",
         "channel": "vector"},
        {"id": published_id, "job_portrait_id": published_id,
         "position_name": f"FORGED VECTOR TITLE {nonce}", "text": f"PRIVATE {nonce}",
         "publication_version": "v1", "content_sha256": content_sha256(snapshot),
         "channel": "vector"},
        {"id": source_only_id, "job_portrait_id": source_only_id,
         "position_name": f"SOURCE ONLY {nonce}", "channel": "vector"},
        {"id": f"role:{role_name}", "role_name": role_name,
         "position_name": f"FORGED ROLE TITLE {nonce}", "text": f"PRIVATE {nonce}",
         "channel": "vector"},
        {"id": f"role:NONEXISTENT {nonce}", "role_name": f"NONEXISTENT {nonce}",
         "position_name": f"FORGED UNKNOWN ROLE {nonce}", "channel": "vector"},
    ])
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = requests.Session()
    session.trust_env = False
    url = f"http://127.0.0.1:{server.server_port}/api/match/hybrid-candidates"
    try:
        response = session.post(url, json={"skills": ["Python"], "top_k": 10,
                                           "enable_graph_hop": False}, timeout=5)
        assert response.status_code == 200, response.text
        candidates = response.json()["data"]["candidates"]
        ids = {item["id"] for item in candidates}
        assert unpublished_id not in ids
        assert source_only_id not in ids
        assert f"role:NONEXISTENT {nonce}" not in ids
        role = next(item for item in candidates if item["id"] == f"role:{role_name}")
        assert role["position_name"] == role_name
        assert role["candidate_kind"] == "role"
        assert role["match_eligible"] is False
        assert role["job_portrait_id"] is None
        verified = next(item for item in candidates if item["id"] == published_id)
        assert verified["position_name"] == published_name
        assert verified["publication_version"] == "v1"
        assert verified["content_sha256"] == content_sha256(snapshot)
        assert verified["candidate_kind"] == "published_job"
        assert "PRIVATE" not in json.dumps(candidates)

        class FakeMilvus:
            def connect(self):
                return True

            def search(self, *, collection_name, **kwargs):
                assert collection_name == "jd_vectors"
                return [
                    {"metadata": json.dumps({"job_id": unpublished_id}),
                     "text": f"PRIVATE UNREVIEWED {nonce}", "distance": 0.11},
                    {"metadata": json.dumps({"job_id": published_id}),
                     "text": f"PRIVATE STALE INDEX TEXT {nonce}", "distance": 0.12},
                    {"metadata": json.dumps({"job_portrait_id": published_id,
                                              "publication_version": "v1",
                                              "content_sha256": content_sha256(snapshot)}),
                     "text": f"PRIVATE INDEX TEXT {nonce}", "distance": 0.22},
                    {"metadata": json.dumps({"job_id": source_only_id}),
                     "text": f"PRIVATE SOURCE TEXT {nonce}", "distance": 0.33},
                ]

        monkeypatch.setattr("app.services.db.get_milvus_client", lambda: FakeMilvus())
        monkeypatch.setattr(pipeline_ext_vector, "_generate_embeddings",
                            lambda texts: ([[0.0]], False))
        vector_url = f"http://127.0.0.1:{server.server_port}/api/pipeline/ext/vector/search"
        vector_read = session.post(vector_url, json={"query": "Python", "collection": "jd"},
                                   timeout=5)
        assert vector_read.status_code == 200, vector_read.text
        jd_hits = vector_read.json()["data"]["results"].get("jd_vectors", [])
        assert len(jd_hits) == 1
        assert jd_hits[0]["job_portrait_id"] == published_id
        assert jd_hits[0]["position_name"] == published_name
        assert jd_hits[0]["content_sha256"] == content_sha256(snapshot)
        assert "PRIVATE" not in json.dumps(jd_hits)

        db.execute_write("UPDATE job_catalog_publication SET content_sha256=:digest "
                         "WHERE job_portrait_id=:id AND publication_version='v1'",
                         {"id": published_id, "digest": "0" * 64})
        corrupt = session.post(url, json={"skills": ["Python"], "enable_graph_hop": False},
                               timeout=5)
        assert corrupt.status_code == 503, corrupt.text
        db.execute_write("UPDATE job_catalog_publication SET content_sha256=:digest "
                         "WHERE job_portrait_id=:id AND publication_version='v1'",
                         {"id": published_id, "digest": content_sha256(snapshot)})

        db.execute_write(
            "INSERT INTO job_catalog_publication "
            "(job_portrait_id,publication_version,snapshot_json,content_sha256,status,published_at) "
            "VALUES (:id,'v2',:snapshot,:digest,'revoked',NOW())",
            {"id": published_id, "snapshot": json.dumps(snapshot, ensure_ascii=False),
             "digest": content_sha256(snapshot)},
        )
        after = session.post(url, json={"skills": ["Python"], "enable_graph_hop": False},
                             timeout=5)
        assert after.status_code == 200, after.text
        assert all(item["id"] != published_id for item in after.json()["data"]["candidates"])
        after_vector = session.post(vector_url, json={"query": "Python", "collection": "jd"},
                                    timeout=5)
        assert after_vector.status_code == 200, after_vector.text
        assert not after_vector.json()["data"]["results"].get("jd_vectors")
    finally:
        session.close()
        server.shutdown()
        thread.join(timeout=5)
        db.execute_write("DELETE FROM job_catalog_publication WHERE job_portrait_id=:id",
                         {"id": published_id})
        db.execute_write("DELETE FROM job_portrait_4d WHERE portrait_id=:id",
                         {"id": unpublished_id})
        db.execute_write("DELETE FROM career_insights WHERE role_name=:role",
                         {"role": role_name})
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_catalog_publication "
                                 "WHERE job_portrait_id=:id", {"id": published_id})["n"] == 0
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_portrait_4d "
                                 "WHERE portrait_id=:id", {"id": unpublished_id})["n"] == 0
