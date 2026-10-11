from types import SimpleNamespace
import json

from app.services.public_skill_retrieval import retrieve_public_skill_candidates, INDEX_NAME
from app.services.public_skill_graph import SOURCE_ID


def indexes():
    return [{"name": INDEX_NAME, "type": "FULLTEXT", "state": "ONLINE",
             "labelsOrTypes": ["PublicSkill"], "properties": ["name", "aliases", "category"]}]


def test_large_catalog_uses_bounded_recall_not_full_dump():
    catalog = [{"name": "Skill" + str(i), "source_id": SOURCE_ID, "category": "test"}
               for i in range(1000)]
    calls = []
    def query(sql, params, read_only):
        assert read_only
        calls.append(sql)
        if sql.startswith("SHOW INDEXES"):
            return indexes()
        assert "labels(s) = ['PublicSkill']" in sql
        assert "s.visibility = 'public'" in sql
        assert "LIMIT $limit" in sql
        # Simulate index lookup near the END of a large catalog. No first-200 bias.
        if sql.startswith("MATCH"):
            return [catalog[999]]
        assert "{limit:41}" in sql
        return [catalog[999], catalog[998], catalog[997], catalog[996]]
    rows, coverage = retrieve_public_skill_candidates(SimpleNamespace(execute_query=query), [{"name": "Skill999"}])
    assert [r["name"] for r in rows] == ["Skill999", "Skill998", "Skill997"]
    assert coverage["mentions"][0]["more_candidates"] is True
    assert coverage["candidate_count"] == 3
    assert coverage["exhaustive"] is False
    assert not any("LIMIT 201" in sql for sql in calls)
    from app.services.resume_entity_linking import link_resume_mentions
    model = SimpleNamespace(chat=lambda **kw: json.dumps({"decisions": [{
        "mention_index": 0, "decision": "equivalent", "candidate_index": 0,
        "rationale": "原文与检索候选指同一技能"}]}))
    linked = link_resume_mentions(model, [{"name": "Skill999", "evidence": "Skill999"}],
                                  "Skill999", client=SimpleNamespace(execute_query=query))
    assert linked["status"] == "proposed"
    assert linked["candidates"][0]["name"] == "Skill999"


def test_untrusted_index_does_not_query_legacy_and_returns_exact_only():
    def query(sql, params, read_only):
        if sql.startswith("SHOW INDEXES"):
            return [{**indexes()[0], "labelsOrTypes": ["Skill"]}]
        assert sql.startswith("MATCH (s:PublicSkill")
        return [{"name": "Python", "source_id": SOURCE_ID, "category": "language"}]
    rows, coverage = retrieve_public_skill_candidates(SimpleNamespace(execute_query=query), [{"name": "Python"}])
    assert len(rows) == 1
    assert coverage["strategy"] == "exact_only"
    assert coverage["degraded"] is True


def test_literal_query_and_failed_fulltext_preserve_explicit_degradation():
    def query(sql, params, read_only):
        if sql.startswith("SHOW INDEXES"):
            return indexes()
        if sql.startswith("MATCH"):
            return []
        assert params["query"].startswith('"') and params["query"].endswith('"')
        assert '\\"' in params["query"]
        raise RuntimeError("index unavailable")
    rows, coverage = retrieve_public_skill_candidates(SimpleNamespace(execute_query=query), [{"name": 'x" OR *:*'}])
    assert rows == []
    assert coverage["degraded"] is True
    assert coverage["mentions"][0]["status"] == "retrieval_failed"
