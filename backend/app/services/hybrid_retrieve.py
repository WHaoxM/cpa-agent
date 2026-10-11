"""
Hybrid retrieve for job/role candidates — DOES NOT write match scores.

Pipeline: vector (Milvus) + keyword (MySQL) → RRF fuse → optional Neo4j ability hop.
Deterministic SkillMatcher scores stay elsewhere (matcher.py / match_result rows).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..utils.logger import get_logger

logger = get_logger("cpa-agent.hybrid_retrieve")

RRF_K = 60


def rrf_fuse(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = RRF_K,
) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion over id lists (rank 0 = best)."""
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked):
            if not doc_id:
                continue
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _norm_skill(name: str) -> str:
    return re.sub(r"\s+", "", (name or "").strip().lower())


def _skill_names(skills: Sequence[Any]) -> List[str]:
    out: List[str] = []
    for s in skills or []:
        if isinstance(s, str):
            n = s.strip()
        elif isinstance(s, dict):
            n = str(s.get("name") or "").strip()
        else:
            n = ""
        if n:
            out.append(n)
    return out


def keyword_retrieve_jobs(
    skill_names: Sequence[str],
    *,
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    """MySQL keyword overlap on job_portrait / career_insights core skills."""
    names = [n for n in skill_names if n]
    if not names:
        return []
    try:
        from .db import get_mysql_client

        mysql = get_mysql_client()
    except Exception as e:
        logger.warning("keyword retrieve: mysql unavailable: %s", e)
        return []

    hits: List[Dict[str, Any]] = []
    try:
        # Prefer structured job portraits when present
        rows = mysql.execute(
            """
            SELECT portrait_id, position_name, portrait_json
            FROM job_portrait_4d
            ORDER BY updated_at DESC
            LIMIT 200
            """
        ) or []
    except Exception:
        rows = []

    student_set = {_norm_skill(n) for n in names}
    for row in rows:
        portrait_json = row.get("portrait_json") or {}
        if isinstance(portrait_json, str):
            try:
                portrait_json = json.loads(portrait_json)
            except Exception:
                portrait_json = {}

        # 从portrait_json中提取required_skills
        d2_req = portrait_json.get("D2_requirements", {})
        raw = d2_req.get("required_skills", [])
        req_names = []
        for item in raw:
            if isinstance(item, str):
                req_names.append(item)
            elif isinstance(item, dict) and item.get("name"):
                req_names.append(str(item["name"]))
            elif isinstance(item, dict) and item.get("skill"):
                req_names.append(str(item["skill"]))
        overlap = [r for r in req_names if _norm_skill(r) in student_set]
        if not overlap:
            continue
        pid = str(row.get("portrait_id") or "")
        hits.append(
            {
                "id": pid,
                "job_portrait_id": pid,
                "position_name": row.get("position_name") or pid,
                "channel": "keyword",
                "matched_keywords": overlap[:12],
                "overlap": len(overlap),
            }
        )
    hits.sort(key=lambda h: h["overlap"], reverse=True)

    if hits:
        return hits[:top_k]

    # Fallback: career_insights.core_skills
    try:
        insight_rows = mysql.execute(
            """
            SELECT role_name, core_skills, category, demand_level
            FROM career_insights
            LIMIT 200
            """
        ) or []
    except Exception:
        insight_rows = []

    for row in insight_rows:
        raw = row.get("core_skills") or "[]"
        try:
            core = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            core = []
        if not isinstance(core, list):
            core = []
        core_names = [str(c) for c in core]
        overlap = [c for c in core_names if _norm_skill(c) in student_set]
        if not overlap:
            continue
        role = str(row.get("role_name") or "")
        hits.append(
            {
                "id": f"role:{role}",
                "job_portrait_id": None,
                "role_name": role,
                "position_name": role,
                "channel": "keyword",
                "matched_keywords": overlap[:12],
                "overlap": len(overlap),
                "category": row.get("category"),
                "demand_level": row.get("demand_level"),
            }
        )
    hits.sort(key=lambda h: h["overlap"], reverse=True)
    return hits[:top_k]


def vector_retrieve_jobs(
    skill_names: Sequence[str],
    *,
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    """Milvus semantic search over jd_vectors / skill_vectors — retrieval only."""
    query = " ".join(skill_names[:40]).strip()
    if not query:
        return []
    try:
        from .embedding_service import get_embedding_service
        from .db import get_milvus_client

        emb = get_embedding_service()
        if not emb.is_available():
            return []
        vectors = emb.encode([query])
        if not vectors:
            return []
        milvus = get_milvus_client()
        if not milvus.connect():
            return []
        collection = "jd_vectors"
        if not milvus._client.has_collection(collection):
            collection = "skill_vectors"
        raw = milvus.search(
            collection_name=collection,
            query_vector=vectors[0],
            top_k=top_k,
        ) or []
    except Exception as e:
        logger.warning("vector retrieve failed: %s", e)
        return []

    out: List[Dict[str, Any]] = []
    for i, hit in enumerate(raw):
        meta_raw = hit.get("metadata") or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
        except Exception:
            meta = {}
        doc_id = (
            str(meta.get("portrait_id") or meta.get("job_portrait_id") or "")
            or str(meta.get("role_name") or meta.get("skill_name") or hit.get("text") or f"vec_{i}")
        )
        if meta.get("role_name") and not str(doc_id).startswith("role:"):
            if not meta.get("portrait_id"):
                doc_id = f"role:{meta['role_name']}"
        out.append(
            {
                "id": doc_id,
                "job_portrait_id": meta.get("portrait_id") or meta.get("job_portrait_id"),
                "role_name": meta.get("role_name"),
                "position_name": meta.get("position_name") or meta.get("role_name") or doc_id,
                "channel": "vector",
                "distance": hit.get("distance"),
                "text": (hit.get("text") or "")[:200],
            }
        )
    return out


def graph_hop_for_skills(
    role: str,
    skill_names: Sequence[str],
    *,
    max_hops: int = 2,
    limit: int = 24,
) -> Dict[str, Any]:
    """1–2 hop expansion on AbilityNode / COVERS courses for explainability."""
    from .ability_graph_service import resolve_ability_role

    resolved = resolve_ability_role(role) if role else None
    if not resolved:
        return {"role": role or None, "resolved_role": None, "paths": [], "related_courses": []}

    try:
        from .db import get_neo4j_client

        neo4j = get_neo4j_client()
    except Exception as e:
        logger.warning("graph hop: neo4j unavailable: %s", e)
        return {"role": role, "resolved_role": resolved, "paths": [], "related_courses": []}

    names = list(skill_names)[:40]
    paths: List[Dict[str, Any]] = []
    courses: List[Dict[str, Any]] = []

    try:
        # Hop-1: ability nodes matching skill names
        node_rows = neo4j.execute_query(
            """
            MATCH (a:AbilityNode {role: $role})
            WHERE a.name IN $names
               OR any(n IN $names WHERE toLower(a.name) CONTAINS toLower(n)
                                   OR toLower(n) CONTAINS toLower(a.name))
            RETURN a.id AS id, a.name AS name, a.level AS level, a.group AS group_name
            LIMIT $limit
            """,
            {"role": resolved, "names": names, "limit": limit},
        ) or []
        seed_ids = [r["id"] for r in node_rows if r.get("id")]
        for r in node_rows:
            paths.append(
                {
                    "hop": 0,
                    "node_id": r.get("id"),
                    "name": r.get("name"),
                    "level": r.get("level"),
                    "group": r.get("group_name"),
                }
            )

        if seed_ids and max_hops >= 1:
            nbr = neo4j.execute_query(
                """
                MATCH (s:AbilityNode {role: $role})-[rel:RELATES_TO]-(t:AbilityNode {role: $role})
                WHERE s.id IN $ids
                RETURN s.id AS source, s.name AS source_name,
                       t.id AS target, t.name AS target_name,
                       type(rel) AS rel_type, rel.relation AS relation
                LIMIT $limit
                """,
                {"role": resolved, "ids": seed_ids, "limit": limit},
            ) or []
            for e in nbr:
                paths.append(
                    {
                        "hop": 1,
                        "source": e.get("source_name"),
                        "target": e.get("target_name"),
                        "relation": e.get("relation") or e.get("rel_type"),
                    }
                )

        if seed_ids:
            course_rows = neo4j.execute_query(
                """
                MATCH (s:AbilityNode {role: $role})-[rel:COVERS]->(c:CourseGraphNode {role: $role})
                WHERE s.id IN $ids
                RETURN s.name AS skill, c.id AS course_id, c.title AS title,
                       c.difficulty AS difficulty, rel.relation AS relation
                LIMIT $limit
                """,
                {"role": resolved, "ids": seed_ids, "limit": min(limit, 16)},
            ) or []
            for c in course_rows:
                courses.append(
                    {
                        "skill": c.get("skill"),
                        "course_id": c.get("course_id"),
                        "title": c.get("title"),
                        "difficulty": c.get("difficulty"),
                        "relation": c.get("relation"),
                    }
                )
    except Exception as e:
        logger.warning("graph hop query failed: %s", e)

    return {
        "role": role,
        "resolved_role": resolved,
        "paths": paths[:limit],
        "related_courses": courses,
    }


def hybrid_recommend(
    skills: Sequence[Any],
    *,
    preferred_role: Optional[str] = None,
    top_k: int = 10,
    enable_graph_hop: bool = True,
) -> Dict[str, Any]:
    """
    Return fused candidates with per-channel components.
    Never includes deterministic match total_score.
    """
    names = _skill_names(skills)
    kw = keyword_retrieve_jobs(names, top_k=max(top_k * 2, 20))
    vec = vector_retrieve_jobs(names, top_k=max(top_k * 2, 20))

    by_id: Dict[str, Dict[str, Any]] = {}
    for item in kw + vec:
        doc_id = str(item.get("id") or "")
        if not doc_id:
            continue
        cur = by_id.get(doc_id) or {
            "id": doc_id,
            "job_portrait_id": item.get("job_portrait_id"),
            "role_name": item.get("role_name"),
            "position_name": item.get("position_name") or item.get("role_name") or doc_id,
            "channels": [],
            "matched_keywords": [],
            "components": {"vector": 0.0, "keyword": 0.0, "graph": 0.0, "rrf": 0.0},
        }
        ch = item.get("channel")
        if ch and ch not in cur["channels"]:
            cur["channels"].append(ch)
        if item.get("matched_keywords"):
            seen = set(cur["matched_keywords"])
            for k in item["matched_keywords"]:
                if k not in seen:
                    cur["matched_keywords"].append(k)
                    seen.add(k)
        if item.get("job_portrait_id") and not cur.get("job_portrait_id"):
            cur["job_portrait_id"] = item["job_portrait_id"]
        if item.get("role_name") and not cur.get("role_name"):
            cur["role_name"] = item["role_name"]
        by_id[doc_id] = cur

    kw_ids = [str(h["id"]) for h in kw]
    vec_ids = [str(h["id"]) for h in vec]
    fused = rrf_fuse([vec_ids, kw_ids])

    # component ranks for explainability
    kw_rank = {d: i for i, d in enumerate(kw_ids)}
    vec_rank = {d: i for i, d in enumerate(vec_ids)}

    candidates: List[Dict[str, Any]] = []
    for doc_id, rrf_score in fused[:top_k]:
        base = by_id.get(doc_id) or {
            "id": doc_id,
            "position_name": doc_id,
            "channels": [],
            "matched_keywords": [],
            "components": {},
        }
        comps = {
            "vector": 1.0 / (RRF_K + vec_rank[doc_id] + 1) if doc_id in vec_rank else 0.0,
            "keyword": 1.0 / (RRF_K + kw_rank[doc_id] + 1) if doc_id in kw_rank else 0.0,
            "graph": 0.0,
            "rrf": float(rrf_score),
        }
        base = {**base, "components": comps, "retrieve_score": float(rrf_score)}
        candidates.append(base)

    graph_payload: Optional[Dict[str, Any]] = None
    if enable_graph_hop:
        role_hint = preferred_role or ""
        if not role_hint and candidates:
            role_hint = str(
                candidates[0].get("role_name")
                or candidates[0].get("position_name")
                or ""
            )
        if role_hint.startswith("role:"):
            role_hint = role_hint[5:]
        graph_payload = graph_hop_for_skills(role_hint, names, max_hops=2)
        if graph_payload.get("paths"):
            for c in candidates[:3]:
                c["components"]["graph"] = min(0.15, 0.02 * len(graph_payload["paths"]))
                if "graph" not in c["channels"]:
                    c["channels"].append("graph")

    return {
        "skills": names,
        "candidates": candidates,
        "graph": graph_payload,
        "note": "retrieve_score is RRF fusion only; deterministic match scores are separate",
    }


def build_match_explain(
    *,
    student_id: str,
    job_portrait_id: str,
    matched_skills: Sequence[str],
    missing_skills: Sequence[str],
    total_score: Optional[float],
    skill_coverage: Optional[float],
    position_name: Optional[str] = None,
    gaps: Optional[List[Dict[str, Any]]] = None,
    student_skill_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Explain layer — never mutates total_score / skill_coverage."""
    student_names = list(student_skill_names or matched_skills)
    missing = list(missing_skills or [])
    matched = list(matched_skills or [])

    keyword_coverage = {
        "matched": matched,
        "missing": missing,
        "matched_count": len(matched),
        "missing_count": len(missing),
        "highlight_missing": missing[:12],
    }

    evidence: List[Dict[str, Any]] = []
    for s in matched[:20]:
        evidence.append(
            {
                "skill": s,
                "status": "covered",
                "span": s,
                "source": "skill_overlap",
            }
        )
    for s in missing[:20]:
        evidence.append(
            {
                "skill": s,
                "status": "gap",
                "span": s,
                "source": "job_required",
            }
        )

    role_for_graph = position_name or job_portrait_id
    graph = graph_hop_for_skills(role_for_graph, student_names + missing, max_hops=2)

    return {
        "student_id": student_id,
        "job_portrait_id": job_portrait_id,
        "position_name": position_name,
        # Scores are pass-through from deterministic store — not recomputed here
        "total_score": total_score,
        "skill_coverage": skill_coverage,
        "scores_authoritative": True,
        "keyword_coverage": keyword_coverage,
        "evidence": evidence,
        "gaps": gaps or [],
        "graph_paths": graph.get("paths") or [],
        "related_courses": graph.get("related_courses") or [],
        "resolved_role": graph.get("resolved_role"),
    }
