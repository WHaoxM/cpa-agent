"""
Hybrid retrieve for job/role candidates — DOES NOT write match scores.

Pipeline: vector (Milvus) + keyword (MySQL) → RRF fuse → optional Neo4j ability hop.
Deterministic SkillMatcher scores stay elsewhere (matcher.py / match_result rows).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..utils.logger import get_logger

logger = get_logger("cpa-agent.hybrid_retrieve")

RRF_K = 60


class CandidateCatalogUnavailable(RuntimeError):
    """The public source of candidate truth could not be verified."""


class VectorRetrievalUnavailable(RuntimeError):
    """Vector dependency failed; an empty result would misstate source coverage."""


def _matches_publication_binding(metadata: Dict[str, Any], publication: Dict[str, Any]) -> bool:
    """An index hit can rank only the exact current snapshot it indexed."""
    return (isinstance(metadata.get("publication_version"), str)
            and bool(metadata["publication_version"])
            and isinstance(metadata.get("content_sha256"), str)
            and bool(metadata["content_sha256"])
            and metadata["publication_version"] == publication["publication_version"]
            and metadata["content_sha256"] == publication["content_sha256"])


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
    """Keyword overlap on validated publications, then public role insights."""
    names = [n for n in skill_names if n]
    if not names:
        return []
    from .db import get_mysql_client
    from .job_catalog_listing import list_published_job_candidates
    from .job_publication import read_published_job

    try:
        mysql = get_mysql_client()
    except Exception as exc:
        raise CandidateCatalogUnavailable("已发布岗位目录暂不可访问") from exc

    hits: List[Dict[str, Any]] = []
    try:
        first = list_published_job_candidates(mysql, limit=50, offset=0)
        if first.get("status") == "unavailable":
            raise CandidateCatalogUnavailable("已发布岗位目录暂不可校验")
        rows = list(first.get("items") or [])
        version = first.get("catalog_version")
        total = int(first.get("total") or 0)
        while len(rows) < total:
            page = list_published_job_candidates(mysql, limit=50, offset=len(rows))
            if (page.get("status") == "unavailable"
                    or page.get("catalog_version") != version
                    or not page.get("items")):
                raise CandidateCatalogUnavailable("岗位目录在召回期间发生变化，请重试")
            rows.extend(page["items"])
    except CandidateCatalogUnavailable:
        raise
    except Exception as exc:
        raise CandidateCatalogUnavailable("已发布岗位目录暂不可校验") from exc
    student_set = {_norm_skill(n) for n in names}
    for row in rows:
        try:
            pid = row["job_portrait_id"]
            publication = read_published_job(mysql, pid)
            if publication is None or publication["content_sha256"] != row["content_sha256"]:
                raise CandidateCatalogUnavailable("岗位发布版本在召回期间发生变化，请重试")
            snapshot = publication["snapshot_json"]
            req_names = [item["name"] for item in snapshot["required_skills"]]
        except CandidateCatalogUnavailable:
            raise
        except Exception as exc:
            raise CandidateCatalogUnavailable("岗位发布快照暂不可校验") from exc
        overlap = [r for r in req_names if _norm_skill(r) in student_set]
        if not overlap:
            continue
        hits.append(
            {
                "id": pid,
                "job_portrait_id": pid,
                "position_name": snapshot["position_name"],
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
    except Exception as exc:
        raise CandidateCatalogUnavailable("公共职业方向目录暂不可读取") from exc

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
            raise VectorRetrievalUnavailable("向量模型暂不可用")
        vectors = emb.encode([query])
        if not vectors:
            raise VectorRetrievalUnavailable("向量模型未返回有效结果")
        milvus = get_milvus_client()
        if not milvus.connect():
            raise VectorRetrievalUnavailable("向量索引暂不可连接")
        collection = "jd_vectors"
        if not milvus._client.has_collection(collection):
            collection = "skill_vectors"
        raw = milvus.search(
            collection_name=collection,
            query_vector=vectors[0],
            top_k=top_k,
            strict=True,
        ) or []
    except Exception as e:
        logger.warning("vector retrieve failed: %s", e)
        if isinstance(e, VectorRetrievalUnavailable):
            raise
        raise VectorRetrievalUnavailable("向量检索暂不可用") from e

    out: List[Dict[str, Any]] = []
    for i, hit in enumerate(raw):
        meta_raw = hit.get("metadata") or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            continue
        if collection == "jd_vectors" and not meta.get("job_portrait_id"):
            # A public role name in old JD metadata is not a role-vector source.
            continue
        doc_id = (
            str(meta.get("portrait_id") or meta.get("job_portrait_id") or meta.get("job_id") or "")
            or str(meta.get("role_name") or meta.get("skill_name") or hit.get("text") or f"vec_{i}")
        )
        if meta.get("role_name") and not str(doc_id).startswith("role:"):
            if not meta.get("portrait_id"):
                doc_id = f"role:{meta['role_name']}"
        out.append(
            {
                "id": doc_id,
                "job_portrait_id": meta.get("portrait_id") or meta.get("job_portrait_id") or meta.get("job_id"),
                "publication_version": meta.get("publication_version"),
                "content_sha256": meta.get("content_sha256"),
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

    try:
        resolved = resolve_ability_role(role) if role else None
    except Exception as exc:
        logger.warning("graph hop role resolution unavailable: %s", exc)
        return {"role": role or None, "resolved_role": None, "paths": [],
                "related_courses": [], "status": "unavailable"}
    if not resolved:
        return {"role": role or None, "resolved_role": None, "paths": [],
                "related_courses": [], "status": "unmapped"}

    try:
        from .db import get_neo4j_client

        neo4j = get_neo4j_client()
    except Exception as e:
        logger.warning("graph hop: neo4j unavailable: %s", e)
        return {"role": role, "resolved_role": resolved, "paths": [],
                "related_courses": [], "status": "unavailable"}

    names = list(skill_names)[:40]
    paths: List[Dict[str, Any]] = []
    courses: List[Dict[str, Any]] = []
    status = "complete"

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
        status = "unavailable"

    return {
        "role": role,
        "resolved_role": resolved,
        "paths": paths[:limit],
        "related_courses": courses,
        "status": status,
    }


def _safe_retrieval_hits(mysql, hits, skill_names):
    """Replace index metadata with current authoritative public projections."""
    from .job_publication import read_published_job

    normalized_skills = {_norm_skill(name) for name in skill_names}
    safe = []
    seen_ids = set()
    for item in hits:
        raw_id = item.get("id")
        if not isinstance(raw_id, str) or not raw_id or len(raw_id) > 256:
            continue
        job_id = item.get("job_portrait_id")
        role_name = item.get("role_name")
        if not job_id and not role_name and raw_id.startswith("role:"):
            role_name = raw_id[5:]
        if not job_id and not role_name:
            job_id = raw_id
        channel = item.get("channel")
        if channel not in {"keyword", "vector"}:
            continue
        try:
            if job_id:
                if not isinstance(job_id, str) or not 1 <= len(job_id) <= 64:
                    continue
                if job_id in seen_ids:
                    continue
                publication = read_published_job(mysql, job_id)
                if publication is None:
                    continue
                if channel == "vector" and not _matches_publication_binding(item, publication):
                    continue
                snapshot = publication["snapshot_json"]
                matched = [skill["name"] for skill in snapshot["required_skills"]
                           if _norm_skill(skill["name"]) in normalized_skills]
                safe.append({"id": job_id, "job_portrait_id": job_id,
                             "position_name": snapshot["position_name"],
                             "publication_status": "published",
                             "publication_version": publication["publication_version"],
                             "content_sha256": publication["content_sha256"],
                             "source_uri": snapshot["source_uri"],
                             "vacancy_validity": "unknown",
                             "candidate_kind": "published_job", "match_eligible": True,
                             "matched_keywords": matched[:12], "channel": channel})
                seen_ids.add(job_id)
            elif isinstance(role_name, str) and 1 <= len(role_name) <= 200:
                row = mysql.execute_single(
                    "SELECT role_name FROM career_insights WHERE role_name=:name LIMIT 1",
                    {"name": role_name})
                if row is None:
                    continue
                role = row["role_name"]
                if f"role:{role}" in seen_ids:
                    continue
                safe.append({"id": f"role:{role}", "job_portrait_id": None,
                             "role_name": role, "position_name": role,
                             "candidate_kind": "role", "match_eligible": False,
                             "matched_keywords": item.get("matched_keywords", [])[:12]
                             if channel == "keyword" else [], "channel": channel})
                seen_ids.add(f"role:{role}")
        except Exception as exc:
            raise CandidateCatalogUnavailable("候选来源校验失败，请稍后重试") from exc
    return safe


def project_public_jd_vector_hits(mysql, raw_hits):
    """Replace historical JD vector text with current publication projections."""
    from .job_catalog_listing import list_published_job_candidates
    from .job_publication import read_published_job

    catalog = list_published_job_candidates(mysql, limit=1, offset=0)
    if catalog.get("status") == "unavailable":
        raise CandidateCatalogUnavailable("已发布岗位目录暂不可校验")
    projected = []
    seen = set()
    for hit in raw_hits or []:
        if not isinstance(hit, dict):
            continue
        raw_meta = hit.get("metadata")
        try:
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        job_id = meta.get("job_portrait_id")
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 64 or job_id in seen:
            continue
        try:
            publication = read_published_job(mysql, job_id)
        except Exception as exc:
            raise CandidateCatalogUnavailable("向量命中岗位的发布快照无法校验") from exc
        if publication is None:
            continue
        if not _matches_publication_binding(meta, publication):
            continue
        snapshot = publication["snapshot_json"]
        distance = hit.get("distance")
        if (isinstance(distance, bool) or not isinstance(distance, (int, float))
                or not math.isfinite(float(distance))):
            distance = None
        projected.append({"id": job_id, "job_portrait_id": job_id,
                          "position_name": snapshot["position_name"],
                          "publication_status": "published",
                          "publication_version": publication["publication_version"],
                          "content_sha256": publication["content_sha256"],
                          "source_uri": snapshot["source_uri"],
                          "vacancy_validity": "unknown", "match_eligible": True,
                          "candidate_kind": "published_job", "distance": distance})
        seen.add(job_id)
    return projected


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
    kw_raw = keyword_retrieve_jobs(names, top_k=max(top_k * 2, 20))
    failed_sources: List[str] = []
    try:
        vec_raw = vector_retrieve_jobs(names, top_k=max(top_k * 2, 20))
    except VectorRetrievalUnavailable:
        vec_raw = []
        failed_sources.append("vector")
    try:
        from .db import get_mysql_client
        mysql = get_mysql_client()
        kw = _safe_retrieval_hits(mysql, kw_raw, names)
        vec = _safe_retrieval_hits(mysql, vec_raw, names)
    except CandidateCatalogUnavailable:
        raise
    except Exception as exc:
        raise CandidateCatalogUnavailable("候选来源暂不可校验") from exc

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
            "candidate_kind": item.get("candidate_kind"),
            "match_eligible": item.get("match_eligible"),
            "publication_status": item.get("publication_status"),
            "publication_version": item.get("publication_version"),
            "content_sha256": item.get("content_sha256"),
            "source_uri": item.get("source_uri"),
            "vacancy_validity": item.get("vacancy_validity"),
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
    for doc_id, rrf_score in fused:
        base = by_id.get(doc_id) or {
            "id": doc_id,
            "position_name": doc_id,
            "channels": [],
            "matched_keywords": [],
            "components": {},
        }
        try:
            if base.get("candidate_kind") == "published_job":
                from .job_publication import read_published_job
                current = read_published_job(mysql, doc_id)
                if current is None or current["content_sha256"] != base.get("content_sha256"):
                    continue
            elif base.get("candidate_kind") == "role":
                if mysql.execute_single(
                    "SELECT role_name FROM career_insights WHERE role_name=:name LIMIT 1",
                    {"name": base["role_name"]}) is None:
                    continue
            else:
                continue
        except Exception as exc:
            raise CandidateCatalogUnavailable("候选在返回前无法再次校验") from exc
        comps = {
            "vector": 1.0 / (RRF_K + vec_rank[doc_id] + 1) if doc_id in vec_rank else 0.0,
            "keyword": 1.0 / (RRF_K + kw_rank[doc_id] + 1) if doc_id in kw_rank else 0.0,
            "graph": 0.0,
            "rrf": float(rrf_score),
        }
        base = {**base, "components": comps, "retrieve_score": float(rrf_score)}
        candidates.append(base)
        if len(candidates) >= top_k:
            break

    graph_payload: Optional[Dict[str, Any]] = None
    graph_status = "disabled"
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
        graph_status = graph_payload.get("status", "unavailable")
        if graph_status == "unavailable":
            failed_sources.append("graph")
        if graph_status == "complete" and graph_payload.get("paths"):
            for c in candidates[:3]:
                c["components"]["graph"] = min(0.15, 0.02 * len(graph_payload["paths"]))
                if "graph" not in c["channels"]:
                    c["channels"].append("graph")

    status = "partial" if failed_sources else ("complete" if candidates else "empty")
    return {
        "status": status,
        "source_coverage": {
            "keyword_catalog": "complete" if kw else "empty",
            "vector": "unavailable" if "vector" in failed_sources else
                      ("complete" if vec else "empty"),
            "graph": graph_status,
        },
        "failed_sources": failed_sources,
        "next_action": "稍后重试不可用的来源" if failed_sources else None,
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
