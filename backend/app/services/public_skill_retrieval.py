"""Bounded retrieval over the published PublicSkill partition, never legacy Skill.

Neo4j owns full-text indexing/ranking. This domain adapter only enforces the
published source boundary and candidate budgets; similarity is not identity.
"""
import hashlib
import json

from .public_skill_graph import SOURCE_ID, _public

INDEX_NAME = "cpa_public_skill_search_v1"
PER_MENTION = 3
MAX_CANDIDATES = 200


def _literal_query(name):
    # Literal phrase: document text cannot inject Lucene field/range operators.
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def retrieve_public_skill_candidates(client, mentions):
    coverage = {"source_version": SOURCE_ID, "index": INDEX_NAME,
                "strategy": "neo4j_fulltext", "per_mention_limit": PER_MENTION,
                "candidate_limit": MAX_CANDIDATES, "exhaustive": False,
                "mentions": [], "degraded": False}
    indexes = client.execute_query(
        "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state "
        "WHERE name=$index RETURN name,type,labelsOrTypes,properties,state",
        {"index": INDEX_NAME}, read_only=True)
    online = any(r.get("type") == "FULLTEXT" and r.get("state") == "ONLINE"
                 and r.get("labelsOrTypes") == ["PublicSkill"]
                 and set(r.get("properties") or []) == {"name", "aliases", "category"}
                 for r in indexes)
    if not online:
        coverage.update(strategy="exact_only", degraded=True, reason="public_fulltext_index_unavailable")
    ranked = []
    for mention in mentions:
        params = {"source_id": SOURCE_ID, "name": mention["name"], "limit": PER_MENTION + 1}
        # Indexed equality channel also works when a name contains punctuation
        # not supported by the analyzer. It never declares a semantic match.
        exact = client.execute_query(
            f"MATCH (s:PublicSkill {{source_id:$source_id, name:$name}}) WHERE {_public('s')} "
            "RETURN s.name AS name,s.category AS category,s.source_id AS source_id LIMIT $limit",
            params, read_only=True)
        hits = list(exact)
        failed = False
        if online:
            try:
                rows = client.execute_query(
                    "CALL db.index.fulltext.queryNodes($index,$query,{limit:41}) YIELD node,score "
                    f"WITH node AS s,score WHERE {_public('s')} "
                    "RETURN s.name AS name,s.category AS category,s.source_id AS source_id "
                    "ORDER BY score DESC,s.name LIMIT $limit",
                    {**params, "index": INDEX_NAME, "query": _literal_query(mention["name"])}, read_only=True)
                hits.extend(rows)
            except Exception:
                failed = True
                coverage["degraded"] = True
        unique = {}
        for row in hits:
            # Validate returned provenance too, so test/adaptor errors fail closed.
            if row.get("source_id") == SOURCE_ID and isinstance(row.get("name"), str):
                unique.setdefault(row["name"], row)
        ranked.append(list(unique.values())[:PER_MENTION])
        coverage["mentions"].append({"mention_index": len(ranked) - 1,
            "retrieved": min(len(unique), PER_MENTION), "more_candidates": len(unique) > PER_MENTION,
            "status": "retrieval_failed" if failed else "ready" if unique else "no_lexical_hit"})
    # Fair bounded union: preserve each mention's best candidate before adding
    # second/third hits. Never let the first mention consume the entire pool.
    pool = {}
    for rank in range(PER_MENTION):
        for hits in ranked:
            if rank < len(hits) and len(pool) < MAX_CANDIDATES:
                pool.setdefault(hits[rank]["name"], hits[rank])
    candidates = list(pool.values())
    by_name = {row["name"]: index for index, row in enumerate(candidates)}
    for item, hits in zip(coverage["mentions"], ranked):
        item["candidate_indices"] = [by_name[row["name"]] for row in hits if row["name"] in by_name]
        item["budget_truncated"] = len(item["candidate_indices"]) < len(hits)
    coverage["candidate_count"] = len(candidates)
    coverage["candidate_sha256"] = hashlib.sha256(json.dumps(candidates, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return candidates, coverage
