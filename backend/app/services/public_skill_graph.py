"""Trusted seed-only skill partition; never promote nodes from the legacy graph.

Runtime extraction writes :Skill, while this reader accepts only the exclusive
:PublicSkill label and a publisher-owned source marker on every node and edge.
The offline publisher is backend/scripts/seed_public_skills.py.
"""

from .db import get_neo4j_client

SOURCE_ID = "repo:public-skills:v1"


def _public(alias):
    return (
        f"labels({alias}) = ['PublicSkill'] "
        f"AND {alias}.source_id = $source_id AND {alias}.visibility = 'public'"
    )


QUERIES = {
    "skills": (
        f"MATCH (s:PublicSkill) WHERE {_public('s')} "
        "RETURN s.name AS name, s.category AS category, s.source_id AS source_id "
        "ORDER BY name LIMIT 50", (),
    ),
    "skill_categories": (
        f"MATCH (s:PublicSkill) WHERE {_public('s')} "
        "RETURN s.category AS category, count(s) AS count "
        "ORDER BY category LIMIT 50", (),
    ),
    "skill_neighbors": (
        "MATCH (s:PublicSkill {name: $entity_name})-[r:PREREQUISITE]-(other:PublicSkill) "
        f"WHERE {_public('s')} AND {_public('other')} AND r.source_id = $source_id "
        "RETURN other.name AS name, other.category AS category, type(r) AS relation, "
        "r.source_id AS source_id ORDER BY name LIMIT 50", ("entity_name",),
    ),
    "recommend": (
        "MATCH (s:PublicSkill)-[r:PREREQUISITE]-(other:PublicSkill) "
        f"WHERE {_public('s')} AND {_public('other')} AND r.source_id = $source_id "
        "AND s.name IN $skill_names AND NOT other.name IN $skill_names "
        "RETURN other.name AS name, other.category AS category, count(r) AS weight, "
        "other.source_id AS source_id ORDER BY weight DESC, name LIMIT $limit",
        ("skill_names", "limit"),
    ),
    "learning_path": (
        # A -[:PREREQUISITE]-> B means A requires B, so learning follows reverse edges.
        "MATCH path=(start:PublicSkill {name: $from_skill})"
        "<-[:PREREQUISITE*1..5]-(target:PublicSkill {name: $to_skill}) "
        f"WHERE all(n IN nodes(path) WHERE {_public('n')}) "
        "AND all(r IN relationships(path) WHERE r.source_id = $source_id) "
        "RETURN [n IN nodes(path) | n.name] AS steps, length(path) AS hops "
        "ORDER BY hops LIMIT 1", ("from_skill", "to_skill"),
    ),
    "subgraph": (
        "MATCH (s:PublicSkill)-[r:PREREQUISITE]->(other:PublicSkill) "
        f"WHERE {_public('s')} AND {_public('other')} AND r.source_id = $source_id "
        "AND s.name IN $skill_names AND other.name IN $skill_names "
        "RETURN s.name AS source, other.name AS target, type(r) AS relation, "
        "r.confidence AS confidence, r.source_id AS source_id "
        "ORDER BY source, target LIMIT 50", ("skill_names",),
    ),
}


def query_public_skills(template, params=None, *, client=None):
    """Return explicit unavailable/empty/ready, rather than a fabricated result."""
    if template not in QUERIES:
        raise ValueError("Unknown public skill template")
    cypher, required = QUERIES[template]
    params = dict(params or {})
    if set(params) != set(required):
        raise ValueError("Invalid public skill parameters")
    for key, value in params.items():
        if key == "limit":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 50:
                raise ValueError("limit must be 1..50")
        elif key == "skill_names":
            if not isinstance(value, list) or not 1 <= len(value) <= 50:
                raise ValueError("skills must contain 1..50 names")
            if any(not isinstance(s, str) or not s.strip() or len(s) > 80 for s in value):
                raise ValueError("Invalid skill name")
        elif not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise ValueError("Invalid skill name")
    client = client or get_neo4j_client()
    count = client.execute_query(
        f"MATCH (s:PublicSkill) WHERE {_public('s')} RETURN count(s) AS count",
        {"source_id": SOURCE_ID}, read_only=True,
    )
    if not count or not count[0].get("count"):
        return {
            "success": False, "status": "unavailable", "rows": [],
            "source_id": SOURCE_ID, "scope": "public_seed",
            "message": "公共技能分区尚未发布；请运行 seed_public_skills.py。未读取混存历史 Skill，不能据此判断无技能。",
        }
    rows = client.execute_query(cypher, {**params, "source_id": SOURCE_ID}, read_only=True)
    return {
        "success": True, "status": "ready" if rows else "empty", "rows": rows,
        "source_id": SOURCE_ID, "scope": "public_seed",
        "coverage": "仅仓库审核种子；不含历史 Skill、个人简历/JD 或完整技能目录",
    }
