"""
能力图谱 / 课程体系图 — Neo4j 读写

节点标签:
- AbilityRole {role}
- AbilityNode {id, name, level, group, heat, parentId, role}
- AbilityEdge stored as RELATES_TO {relation, label, role}
- CourseGraphNode {id, title, group, difficulty, importance, role}
- COVERS {relation, role} AbilityNode -> CourseGraphNode (skill->course)
- SkillTierNode / SKILL_TIER_REL for course-system skill lattice

业务数据只来自导入包或 generate_from_mysql（查表投影），不在此文件写死技能名。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db import get_mysql_client, get_neo4j_client
from ..utils.logger import get_logger

logger = get_logger("cpa-agent.ability_graph")

DEFAULT_PACKAGE = (
    Path(__file__).resolve().parents[2] / "scripts" / "neo4j" / "ability_course_graph.json"
)


def _role_key(role: str) -> str:
    return (role or "").strip()


def resolve_ability_role(role: str) -> Optional[str]:
    """将 FE 展示名 / 别名解析为 Neo4j AbilityRole.role；无匹配返回 None。

    别名来自导入包写入的 AbilityRole.aliases，或 SkillTierNode(tier=job).name，
    不在 Python 里写死岗位文案。
    """
    role = _role_key(role)
    if not role:
        return None
    neo4j = get_neo4j_client()
    exact = neo4j.execute_query(
        "MATCH (r:AbilityRole {role: $role}) RETURN r.role AS role LIMIT 1",
        {"role": role},
    )
    if exact:
        return exact[0]["role"]

    alias_hit = neo4j.execute_query(
        """
        MATCH (r:AbilityRole)
        WHERE $role IN coalesce(r.aliases, [])
        RETURN r.role AS role
        LIMIT 1
        """,
        {"role": role},
    )
    if alias_hit:
        return alias_hit[0]["role"]

    job_hit = neo4j.execute_query(
        """
        MATCH (t:SkillTierNode {tier: 'job'})
        WHERE t.name = $role
        RETURN t.role AS role
        LIMIT 1
        """,
        {"role": role},
    )
    if job_hit and job_hit[0].get("role"):
        return job_hit[0]["role"]
    return None


def clear_role_graph(role: str) -> None:
    neo4j = get_neo4j_client()
    neo4j.execute_query(
        """
        MATCH (n {role: $role})
        WHERE n:AbilityRole OR n:AbilityNode OR n:CourseGraphNode OR n:SkillTierNode
        DETACH DELETE n
        """,
        {"role": role},
        read_only=False,
    )


def import_role_payload(role: str, payload: Dict[str, Any]) -> Dict[str, int]:
    """将单个角色的图写入 Neo4j（先清再写）。"""
    role = _role_key(role)
    clear_role_graph(role)
    neo4j = get_neo4j_client()

    aliases = [
        str(a).strip()
        for a in (payload.get("aliases") or [])
        if str(a).strip()
    ]
    # job 层节点名也作为可解析别名（FE 常用展示名）
    for t in payload.get("skillTierNodes") or []:
        if (t.get("tier") or "") == "job" and t.get("name"):
            name = str(t["name"]).strip()
            if name and name not in aliases and name != role:
                aliases.append(name)

    neo4j.execute_query(
        """
        MERGE (r:AbilityRole {role: $role})
        SET r.updated_at = datetime(), r.aliases = $aliases
        """,
        {"role": role, "aliases": aliases},
        read_only=False,
    )

    nodes = payload.get("nodes") or []
    for n in nodes:
        neo4j.execute_query(
            """
            MERGE (a:AbilityNode {id: $id, role: $role})
            SET a.name = $name, a.level = $level, a.group = $group,
                a.heat = $heat, a.parentId = $parentId
            WITH a
            MATCH (r:AbilityRole {role: $role})
            MERGE (r)-[:HAS_NODE]->(a)
            """,
            {
                "id": n["id"],
                "role": role,
                "name": n.get("name") or "",
                "level": int(n.get("level") or 0),
                "group": n.get("group") or "",
                "heat": n.get("heat"),
                "parentId": n.get("parentId"),
            },
            read_only=False,
        )

    for e in payload.get("edges") or []:
        neo4j.execute_query(
            """
            MATCH (s:AbilityNode {id: $source, role: $role})
            MATCH (t:AbilityNode {id: $target, role: $role})
            MERGE (s)-[rel:RELATES_TO {relation: $relation, role: $role}]->(t)
            SET rel.label = $label
            """,
            {
                "source": e["source"],
                "target": e["target"],
                "relation": e.get("relation") or "belong",
                "label": e.get("label"),
                "role": role,
            },
            read_only=False,
        )

    for c in payload.get("courseNodes") or []:
        neo4j.execute_query(
            """
            MERGE (c:CourseGraphNode {id: $id, role: $role})
            SET c.title = $title, c.group = $group, c.difficulty = $difficulty,
                c.importance = $importance, c.relatedSkillIds = $relatedSkillIds
            """,
            {
                "id": c["id"],
                "role": role,
                "title": c.get("title") or "",
                "group": c.get("group") or "",
                "difficulty": c.get("difficulty") or "",
                "importance": c.get("importance") or "",
                "relatedSkillIds": c.get("relatedSkillIds") or [],
            },
            read_only=False,
        )

    for se in payload.get("skillCourseEdges") or []:
        neo4j.execute_query(
            """
            MATCH (s:AbilityNode {id: $skillId, role: $role})
            MATCH (c:CourseGraphNode {id: $courseId, role: $role})
            MERGE (s)-[rel:COVERS {role: $role}]->(c)
            SET rel.relation = $relation
            """,
            {
                "skillId": se["skillId"],
                "courseId": se["courseId"],
                "relation": se.get("relation") or "core",
                "role": role,
            },
            read_only=False,
        )

    for n in payload.get("skillTierNodes") or []:
        neo4j.execute_query(
            """
            MERGE (t:SkillTierNode {id: $id, role: $role})
            SET t.name = $name, t.tier = $tier, t.heat = $heat,
                t.domainId = $domainId, t.relatedCareers = $relatedCareers,
                t.category = $category
            """,
            {
                "id": n["id"],
                "role": role,
                "name": n.get("name") or "",
                "tier": n.get("tier") or "",
                "heat": n.get("heat"),
                "domainId": n.get("domainId"),
                "relatedCareers": n.get("relatedCareers") or [],
                "category": n.get("category"),
            },
            read_only=False,
        )

    for e in payload.get("skillTierEdges") or []:
        neo4j.execute_query(
            """
            MATCH (s:SkillTierNode {id: $source, role: $role})
            MATCH (t:SkillTierNode {id: $target, role: $role})
            MERGE (s)-[rel:SKILL_TIER_REL {role: $role}]->(t)
            SET rel.relation = $relation, rel.isCareerPath = $isCareerPath
            """,
            {
                "source": e["source"],
                "target": e["target"],
                "relation": e.get("relation") or "prerequisite",
                "isCareerPath": bool(e.get("isCareerPath")),
                "role": role,
            },
            read_only=False,
        )

    return {
        "nodes": len(nodes),
        "edges": len(payload.get("edges") or []),
        "courseNodes": len(payload.get("courseNodes") or []),
        "skillTierNodes": len(payload.get("skillTierNodes") or []),
    }


def load_package(path: Optional[Path] = None) -> Dict[str, Any]:
    package_path = Path(path) if path else DEFAULT_PACKAGE
    data = json.loads(package_path.read_text(encoding="utf-8"))
    roles = data.get("roles") or []
    summary = {}
    for item in roles:
        role = item.get("role")
        if not role:
            continue
        summary[role] = import_role_payload(role, item)
    logger.info("loaded ability/course graph package from %s roles=%s", package_path, list(summary))
    return {"path": str(package_path), "roles": summary}


def get_ability_graph(role: str) -> Optional[Dict[str, Any]]:
    resolved = resolve_ability_role(role)
    if not resolved:
        return None
    role = resolved
    neo4j = get_neo4j_client()
    node_rows = neo4j.execute_query(
        """
        MATCH (:AbilityRole {role: $role})-[:HAS_NODE]->(a:AbilityNode {role: $role})
        RETURN a.id AS id, a.name AS name, a.level AS level, a.group AS group_name,
               a.heat AS heat, a.parentId AS parentId
        ORDER BY a.level, a.id
        """,
        {"role": role},
    )
    if not node_rows:
        return None

    edge_rows = neo4j.execute_query(
        """
        MATCH (:AbilityRole {role: $role})-[:HAS_NODE]->(s:AbilityNode {role: $role})
              -[rel:RELATES_TO {role: $role}]->(t:AbilityNode {role: $role})
        RETURN s.id AS source, t.id AS target, rel.relation AS relation, rel.label AS label
        """,
        {"role": role},
    )
    nodes = []
    for r in node_rows:
        item = {
            "id": r["id"],
            "name": r["name"],
            "level": int(r["level"] or 0),
            "group": r.get("group_name") or "",
        }
        if r.get("heat") is not None:
            item["heat"] = r["heat"]
        if r.get("parentId"):
            item["parentId"] = r["parentId"]
        nodes.append(item)
    edges = [
        {
            "source": e["source"],
            "target": e["target"],
            "relation": e.get("relation") or "belong",
            **({"label": e["label"]} if e.get("label") else {}),
        }
        for e in edge_rows
    ]
    return {"nodes": nodes, "edges": edges, "role": role}


def get_course_system_graph(role: str) -> Optional[Dict[str, Any]]:
    role = _role_key(role)
    ability = get_ability_graph(role)
    if ability is None:
        return None
    role = ability.get("role") or role
    neo4j = get_neo4j_client()

    tier_nodes = neo4j.execute_query(
        """
        MATCH (t:SkillTierNode {role: $role})
        RETURN t.id AS id, t.name AS name, t.tier AS tier, t.heat AS heat,
               t.domainId AS domainId, t.relatedCareers AS relatedCareers,
               t.category AS category
        ORDER BY t.tier, t.id
        """,
        {"role": role},
    )
    tier_edges = neo4j.execute_query(
        """
        MATCH (s:SkillTierNode {role: $role})-[rel:SKILL_TIER_REL {role: $role}]->(t:SkillTierNode {role: $role})
        RETURN s.id AS source, t.id AS target, rel.relation AS relation, rel.isCareerPath AS isCareerPath
        """,
        {"role": role},
    )
    course_nodes = neo4j.execute_query(
        """
        MATCH (c:CourseGraphNode {role: $role})
        RETURN c.id AS id, c.title AS title, c.group AS group_name, c.difficulty AS difficulty,
               c.importance AS importance, c.relatedSkillIds AS relatedSkillIds
        ORDER BY c.id
        """,
        {"role": role},
    )
    skill_course = neo4j.execute_query(
        """
        MATCH (:AbilityRole {role: $role})-[:HAS_NODE]->(s:AbilityNode {role: $role})
              -[rel:COVERS {role: $role}]->(c:CourseGraphNode {role: $role})
        RETURN s.id AS skillId, c.id AS courseId, rel.relation AS relation
        """,
        {"role": role},
    )

    if not tier_nodes and not course_nodes:
        return None

    nodes = []
    for t in tier_nodes:
        item = {
            "id": t["id"],
            "name": t["name"],
            "tier": t.get("tier") or "foundation",
            "heat": t.get("heat") or 0,
        }
        if t.get("domainId"):
            item["domainId"] = t["domainId"]
        if t.get("relatedCareers"):
            item["relatedCareers"] = list(t["relatedCareers"])
        if t.get("category"):
            item["category"] = t["category"]
        nodes.append(item)

    edges = [
        {
            "source": e["source"],
            "target": e["target"],
            "relation": e.get("relation") or "prerequisite",
            **({"isCareerPath": True} if e.get("isCareerPath") else {}),
        }
        for e in tier_edges
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "courseNodes": [
            {
                "id": c["id"],
                "title": c["title"],
                "group": c.get("group_name") or "",
                "difficulty": c.get("difficulty") or "beginner",
                "relatedSkillIds": list(c.get("relatedSkillIds") or []),
                "importance": c.get("importance") or "optional",
            }
            for c in course_nodes
        ],
        "skillCourseEdges": [
            {
                "skillId": s["skillId"],
                "courseId": s["courseId"],
                "relation": s.get("relation") or "core",
            }
            for s in skill_course
        ],
        "role": role,
    }


def generate_from_mysql(role: str) -> Optional[Dict[str, Any]]:
    """从 career_insights + skill_taxonomy + course 投影图并写入 Neo4j。无硬编码技能名。"""
    role = _role_key(role)
    mysql = get_mysql_client()
    insights = mysql.execute(
        """
        SELECT role_name, core_skills, category
        FROM career_insights
        WHERE role_name = :role OR role_name LIKE :role_like
        LIMIT 1
        """,
        {"role": role, "role_like": f"%{role}%"},
    )
    if not insights:
        return None
    row = insights[0]
    resolved = row["role_name"]
    raw_skills = row.get("core_skills")
    if isinstance(raw_skills, str):
        try:
            skills = json.loads(raw_skills)
        except json.JSONDecodeError:
            skills = []
    else:
        skills = raw_skills or []
    if not isinstance(skills, list) or not skills:
        return None

    tax_by_name: Dict[str, Dict[str, Any]] = {}
    for s in skills:
        rows = mysql.execute(
            "SELECT skill_name, dimension, tier, heat_score, category "
            "FROM skill_taxonomy WHERE skill_name = :n LIMIT 1",
            {"n": str(s)},
        )
        if rows:
            tax_by_name[str(s)] = rows[0]

    allowed_groups = {"professional", "position", "cognitive", "general"}
    boards: Dict[str, Dict[str, Any]] = {}
    skill_nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    skill_tier_nodes: List[Dict[str, Any]] = []
    skill_tier_edges: List[Dict[str, Any]] = []

    for i, skill in enumerate(skills):
        skill_name = str(skill)
        tax = tax_by_name.get(skill_name) or {}
        dim = str(tax.get("dimension") or "")
        group = dim if dim in allowed_groups else "professional"
        board_id = f"board-{group}"
        if board_id not in boards:
            boards[board_id] = {
                "id": board_id,
                "name": dim if dim else group,
                "level": 1,
                "group": group,
                "parentId": "job-center",
                "heat": 85,
            }
            edges.append({"source": "job-center", "target": board_id, "relation": "belong"})

        heat = 70
        if tax.get("heat_score") is not None:
            try:
                heat = int(float(tax["heat_score"]) * 100)
            except (TypeError, ValueError):
                heat = 70

        nid = f"skill-{i}"
        skill_nodes.append(
            {
                "id": nid,
                "name": skill_name,
                "level": 2,
                "group": group,
                "parentId": board_id,
                "heat": heat,
            }
        )
        edges.append({"source": board_id, "target": nid, "relation": "belong"})

        tier_names = ["foundation", "junior", "mid", "senior", "job"]
        tier = "junior"
        if tax.get("tier") is not None:
            try:
                tval = int(tax["tier"])
                tier = tier_names[min(max(tval - 1, 0), 4)]
            except (TypeError, ValueError):
                tier = "junior"
        skill_tier_nodes.append(
            {
                "id": nid,
                "name": skill_name,
                "tier": tier,
                "heat": heat,
                "category": tax.get("category"),
            }
        )
        if i > 0:
            skill_tier_edges.append(
                {
                    "source": f"skill-{i - 1}",
                    "target": nid,
                    "relation": "prerequisite",
                    "isCareerPath": True,
                }
            )

    ability_nodes = [
        {"id": "job-center", "name": resolved, "level": 0, "group": "job", "heat": 100},
        *boards.values(),
        *skill_nodes,
    ]

    courses = mysql.execute(
        "SELECT course_id, title, category, difficulty FROM course LIMIT 20"
    )
    course_nodes = []
    skill_course_edges = []
    for c in courses:
        cid = str(c["course_id"])
        course_nodes.append(
            {
                "id": cid,
                "title": c.get("title") or cid,
                "group": "foundation-course",
                "difficulty": c.get("difficulty") or "beginner",
                "relatedSkillIds": ["skill-0"] if skills else [],
                "importance": "recommended",
            }
        )
        if skills:
            skill_course_edges.append(
                {"skillId": "skill-0", "courseId": cid, "relation": "recommended"}
            )

    skill_tier_nodes.append(
        {"id": "job-tier", "name": resolved, "tier": "job", "heat": 100}
    )
    last_skill = next(
        (n["id"] for n in reversed(skill_tier_nodes) if str(n["id"]).startswith("skill-")),
        None,
    )
    if last_skill:
        skill_tier_edges.append(
            {
                "source": last_skill,
                "target": "job-tier",
                "relation": "compose",
                "isCareerPath": True,
            }
        )

    payload = {
        "nodes": ability_nodes,
        "edges": edges,
        "courseNodes": course_nodes,
        "skillCourseEdges": skill_course_edges,
        "skillTierNodes": skill_tier_nodes,
        "skillTierEdges": skill_tier_edges,
    }
    stats = import_role_payload(resolved, payload)
    return {"role": resolved, "stats": stats, "source": "mysql_projection"}
