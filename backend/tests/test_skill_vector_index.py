"""Skill taxonomy vector rebuild, including opt-in real MySQL + Milvus Lite."""

import hashlib
import json
import os

import pytest


class DeterministicTestEmbedding:
    """Test-only stable vectors; never used by the production CLI."""

    model_name = "test/deterministic-sha256"

    def __init__(self):
        self.calls = 0

    def encode(self, texts, batch_size=16):
        self.calls += 1
        return [[(hashlib.sha256(text.encode("utf-8")).digest()[i % 32] - 127) / 128
                 for i in range(1024)] for text in texts]


def test_canonical_ignores_unverified_description_and_heat():
    from app.services.skill_vector_index import _canonical

    row = {"id": 7, "skill_name": "Python", "category": "语言",
           "sub_category": "后端", "tier": 2, "dimension": "D2",
           "description": "未经核验的市场份额", "heat_score": 0.99}
    first = _canonical(row, "BAAI/bge-m3")
    second = _canonical({**row, "description": "另一市场叙述", "heat_score": 0.1}, "BAAI/bge-m3")
    assert first == second
    assert "市场" not in first["text"]
    assert first["metadata"]["source"] == "mysql.skill_taxonomy"


@pytest.mark.skipif(os.environ.get("CPA_TEST_LIVE_MILVUS_LITE") != "1",
                    reason="requires isolated Milvus Lite")
def test_reopened_lite_skill_search_loads_once(tmp_path, monkeypatch):
    from app.services.db.milvus_client import MilvusClient

    uri = str(tmp_path / "cold-search.db")
    first = MilvusClient(uri=uri)
    vector = [0.0] * 1024
    vector[0] = 1.0
    try:
        first.insert("skill_vectors", ["技能：测试"], [vector], [{"skill_name": "测试"}])
    finally:
        first._client.close()
        first.disconnect()

    reopened = MilvusClient(uri=uri)
    try:
        assert reopened.connect()
        # Explicitly recreate the persisted cold-start state without relying
        # on Milvus Lite's in-process server cache.
        reopened._client.release_collection("skill_vectors")
        calls = {"state": 0, "load": 0}
        original_state = reopened._client.get_load_state
        original_load = reopened._client.load_collection
        def state(*args, **kwargs):
            calls["state"] += 1
            return original_state(*args, **kwargs)
        def load(*args, **kwargs):
            calls["load"] += 1
            return original_load(*args, **kwargs)
        monkeypatch.setattr(reopened._client, "get_load_state", state)
        monkeypatch.setattr(reopened._client, "load_collection", load)
        assert reopened.search("skill_vectors", vector, top_k=1, strict=True)[0]["text"] == "技能：测试"
        assert reopened.search("skill_vectors", vector, top_k=1, strict=True)[0]["text"] == "技能：测试"
        assert calls == {"state": 1, "load": 1}
        reopened._client.release_collection("skill_vectors")
        assert reopened.search("skill_vectors", vector, top_k=1, strict=True)[0]["text"] == "技能：测试"
        assert calls == {"state": 2, "load": 2}
    finally:
        reopened._client.close()
        reopened.disconnect()


@pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1"
    or os.environ.get("CPA_TEST_LIVE_MILVUS_LITE") != "1",
    reason="requires real local MySQL and isolated Milvus Lite",
)
def test_live_mysql_to_lite_replay_search_and_partial_failure(tmp_path, monkeypatch):
    from app.services.db import get_mysql_client
    from app.services.db.milvus_client import MilvusClient
    from app.services.skill_vector_index import READ_SQL, load_taxonomy, reconcile_skill_vectors

    mysql = get_mysql_client()
    emb = DeterministicTestEmbedding()
    desired = load_taxonomy(mysql, emb.model_name)
    assert len(desired) > 0
    milvus = MilvusClient(uri=str(tmp_path / "skills.db"))
    try:
        first = reconcile_skill_vectors(mysql, milvus, emb)
        assert first == {"source_rows": len(desired), "inserted": len(desired),
                         "deleted": 0, "verified": len(desired), "unmanaged": 0}
        vector = emb.encode([desired[0]["text"]])[0]
        hits = milvus.search("skill_vectors", vector, top_k=1, strict=True)
        assert hits and hits[0]["text"] == desired[0]["text"]
        assert json.loads(hits[0]["metadata"]) == desired[0]["metadata"]
        before_calls = emb.calls
        repeat = reconcile_skill_vectors(mysql, milvus, emb)
        assert repeat["inserted"] == repeat["deleted"] == 0
        assert repeat["verified"] == len(desired)
        assert emb.calls == before_calls
        changed_rows = [dict(row) for row in mysql.execute(READ_SQL)]
        changed_rows[0]["category"] = "测试分类（仅隔离索引）"
        class ChangedRead:
            def execute(self, sql):
                assert sql == READ_SQL
                return changed_rows
        changed = reconcile_skill_vectors(ChangedRead(), milvus, emb)
        assert changed["inserted"] == changed["deleted"] == 1
        restored = reconcile_skill_vectors(mysql, milvus, emb)
        assert restored["inserted"] == restored["deleted"] == 1
        assert restored["verified"] == len(desired)
    finally:
        if milvus._client is not None:
            milvus._client.close()
        milvus.disconnect()

    interrupted = MilvusClient(uri=str(tmp_path / "interrupted.db"))
    original_insert = interrupted.insert
    def partial_insert(collection_name, texts, vectors, metadata):
        original_insert(collection_name, texts[:1], vectors[:1], metadata[:1])
        raise RuntimeError("simulated crash after first durable insert")
    try:
        monkeypatch.setattr(interrupted, "insert", partial_insert)
        with pytest.raises(RuntimeError, match="simulated crash"):
            reconcile_skill_vectors(mysql, interrupted, emb)
        monkeypatch.setattr(interrupted, "insert", original_insert)
        repaired = reconcile_skill_vectors(mysql, interrupted, emb)
        assert repaired["inserted"] == len(desired) - 1
        assert repaired["verified"] == len(desired)
        assert reconcile_skill_vectors(mysql, interrupted, emb)["inserted"] == 0
    finally:
        if interrupted._client is not None:
            interrupted._client.close()
        interrupted.disconnect()
