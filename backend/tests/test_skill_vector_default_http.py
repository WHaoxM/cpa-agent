"""Opt-in public HTTP search against the real, prebuilt local BGE-M3 index."""

import json
import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_SKILL_INDEX") != "1",
    reason="requires the seeded default Milvus Lite index and local BGE-M3 model",
)


def test_public_skill_search_real_embedding_and_index():
    from app import create_app

    response = create_app().test_client().post(
        "/api/pipeline/ext/vector/search",
        json={"query": "Python", "collection": "skill", "top_k": 3},
    )
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result["success"] is True
    assert result["data"]["status"] == "complete"
    assert result["data"]["failed_collections"] == []
    hits = result["data"]["results"]["skill_vectors"]
    assert len(hits) == 3
    first = json.loads(hits[0]["metadata"])
    assert first["skill_name"] == "Python"
    assert first["source"] == "mysql.skill_taxonomy"
    assert first["content_sha256"]
