"""Career explore: Milvus path must use query_vector + metadata skill_name."""

from __future__ import annotations

from app.api.career import _skill_name_from_hit, _milvus_semantic_match


def test_skill_name_from_metadata_json():
    assert (
        _skill_name_from_hit(
            {"metadata": '{"skill_name": "Vue 3", "category": "前端"}', "text": "x"}
        )
        == "Vue 3"
    )


def test_skill_name_from_text_prefix():
    assert _skill_name_from_hit({"text": "React。前端库"}) == "React"


def test_milvus_semantic_match_uses_query_vector(monkeypatch):
    calls = {}

    class FakeEmb:
        def is_available(self):
            return True

        def encode(self, texts):
            return [[0.1, 0.2]]

    class FakeMilvus:
        def connect(self):
            return True

        def search(self, **kwargs):
            calls["search"] = kwargs
            return [
                {
                    "text": "Python。后端语言",
                    "metadata": '{"skill_name": "Python"}',
                    "distance": 0.1,
                }
            ]

    class FakeMysql:
        def execute(self, sql, params):
            assert "Python" in params.values()
            return [{"skill_name": "Python", "related_roles": '["后端开发"]'}]

    monkeypatch.setattr(
        "app.services.embedding_service.get_embedding_service",
        lambda: FakeEmb(),
    )
    monkeypatch.setattr("app.api.career.get_milvus_client", lambda: FakeMilvus())
    monkeypatch.setattr("app.api.career.get_mysql_client", lambda: FakeMysql())

    out = _milvus_semantic_match("想做后端", top_k=3, existing_results=[])
    assert calls["search"]["collection_name"] == "skill_vectors"
    assert "query_vector" in calls["search"]
    assert "query_text" not in calls["search"]
    assert out == [{"role_name": "后端开发", "matched_by": "semantic"}]
