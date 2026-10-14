"""
Role Inference — Extracted Skills → ranked catalog career directions.

Primary signal: Neo4j ``(CareerNode)-[:REQUIRES]->(Skill)`` coverage with
hub-skill IDF (e.g. Python must not dominate every role).

Secondary: ``career_insights.core_skills`` coverage (matched / missing).
Tertiary: ``skill_taxonomy.related_roles`` vote for OOV / sparse graph.

Interest Explore (``GET /explore``) is a different seam — short keywords only.
This module never accepts resume text.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from ..utils.logger import get_logger

logger = get_logger("cpa-agent.career_role_inference")

SkillInput = Union[str, Dict[str, Any]]

# Common extract aliases → Neo4j / taxonomy skill names
_SKILL_ALIASES = {
    "vue": "Vue.js",
    "vue3": "Vue.js",
    "vue.js": "Vue.js",
    "react.js": "React",
    "reactjs": "React",
    "js": "JavaScript",
    "ts": "TypeScript",
    "nodejs": "Node.js",
    "node": "Node.js",
    "k8s": "Kubernetes",
    "langchain": "LangChain",
    "prompt": "Prompt Engineering",
    "prompt engineering": "Prompt Engineering",
    "graphrag": "RAG",
    "向量库": "向量数据库",
    "milvus": "向量数据库",
    "pytorch": "PyTorch",
    "tensorflow": "深度学习",
    "llm": "大模型",
    "agent": "LangChain",
    "agents": "LangChain",
    "ai agent": "LangChain",
}

_DEMAND_PRIOR = {
    "极高": 1.08,
    "高": 1.05,
    "中": 1.0,
    "低": 0.95,
}


def normalize_skill_inputs(skills: Sequence[SkillInput]) -> List[Tuple[str, float]]:
    """Return [(raw_name, weight)] de-duped (max weight)."""
    best: Dict[str, float] = {}
    for item in skills or []:
        if isinstance(item, str):
            name, weight = item.strip(), 0.7
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("skill_name") or "").strip()
            conf = item.get("confidence", item.get("weight", 0.7))
            try:
                weight = float(conf)
            except (TypeError, ValueError):
                weight = 0.7
            weight = max(0.1, min(1.0, weight))
        else:
            continue
        if not name:
            continue
        best[name] = max(best.get(name, 0.0), weight)
    return sorted(best.items(), key=lambda x: (-x[1], x[0]))


def _parse_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return [raw.strip()]
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
        if isinstance(data, str) and data.strip():
            return [data.strip()]
    return []


def canonicalize_skills(
    pairs: Sequence[Tuple[str, float]],
    taxonomy_names: Optional[Sequence[str]] = None,
) -> Dict[str, float]:
    """Map extracted names → catalog names; merge weights."""
    tax_lower = {n.lower(): n for n in (taxonomy_names or []) if n}
    out: Dict[str, float] = {}
    for raw, w in pairs:
        key = raw.strip()
        lower = key.lower()
        canon = (
            _SKILL_ALIASES.get(lower)
            or tax_lower.get(lower)
            or tax_lower.get(re.sub(r"[\s_\-]+", "", lower))
            or key
        )
        out[canon] = max(out.get(canon, 0.0), w)
    return out


def _empty_result(
    preferred_role: Optional[str],
    skill_count: int = 0,
    *,
    evidence: Optional[str] = None,
) -> Dict[str, Any]:
    cands = []
    if preferred_role:
        cands = [
            {
                "role_name": preferred_role,
                "score": 0.55,
                "coverage": 0.0,
                "matched_skills": [],
                "missing_core_skills": [],
                "sources": ["preferred"],
                "category": None,
                "demand_level": None,
            }
        ]
    return {
        "predicted_role": preferred_role or None,
        "confidence": 0.55 if preferred_role else 0.0,
        "candidates": cands,
        "skill_count": skill_count,
        "evidence_summary": evidence
        or ("preferred_role only" if preferred_role else "no skills"),
    }


def _slot(
    acc: Dict[str, Dict[str, Any]],
    role: str,
    *,
    category: Optional[str] = None,
    demand_level: Optional[str] = None,
) -> Dict[str, Any]:
    if role not in acc:
        acc[role] = {
            "role_name": role,
            "score": 0.0,
            "coverage": 0.0,
            "matched_skills": [],
            "missing_core_skills": [],
            "sources": set(),
            "category": category,
            "demand_level": demand_level,
            "_hit_mass": 0.0,
            "_req_mass": 0.0,
        }
    slot = acc[role]
    if category and not slot.get("category"):
        slot["category"] = category
    if demand_level and not slot.get("demand_level"):
        slot["demand_level"] = demand_level
    return slot


def _add_match(slot: Dict[str, Any], skill: str) -> None:
    if skill not in slot["matched_skills"]:
        slot["matched_skills"].append(skill)


def fetch_neo4j_role_requirements(neo4j_client: Any) -> List[Dict[str, Any]]:
    """
    Load all CareerNode → REQUIRES Skill edges.

    Returns rows: {role_name, category, skills: [{name, importance}]}
    """
    cypher = """
    MATCH (c:CareerNode)-[r:REQUIRES]->(s:Skill)
    RETURN c.position_name AS role_name,
           c.category AS category,
           collect({
             name: s.name,
             importance: coalesce(r.importance, 'required')
           }) AS skills
    """
    try:
        if hasattr(neo4j_client, "execute_query"):
            rows = neo4j_client.execute_query(cypher, {}, read_only=True) or []
        else:
            rows = neo4j_client.execute(cypher, {}) or []
        return rows
    except Exception as e:
        logger.warning("Neo4j REQUIRES load failed: %s", e)
        raise


def skill_document_frequency(role_rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """How many roles require each skill (for IDF)."""
    df: Dict[str, int] = {}
    for row in role_rows:
        seen: Set[str] = set()
        for sk in row.get("skills") or []:
            if isinstance(sk, dict):
                name = str(sk.get("name") or "").strip()
            else:
                name = str(sk).strip()
            if name and name not in seen:
                seen.add(name)
                df[name] = df.get(name, 0) + 1
    return df


def idf_weight(skill: str, df: Dict[str, int], n_roles: int) -> float:
    """Down-weight hub skills that appear in many roles."""
    if n_roles <= 0:
        return 1.0
    # smooth IDF; floor so rare skills stay strong, hubs shrink
    return math.log(1.0 + n_roles / (1.0 + df.get(skill, 0)))


def score_neo4j_coverage(
    role_rows: Sequence[Dict[str, Any]],
    skill_weights: Dict[str, float],
) -> Dict[str, Dict[str, Any]]:
    """
    Primary scorer: coverage of REQUIRES skills with IDF × student confidence.

    score ≈ coverage × (1 + mean matched confidence) with hub penalty inside coverage.
    """
    acc: Dict[str, Dict[str, Any]] = {}
    if not role_rows or not skill_weights:
        return acc

    student = {k.lower(): (k, v) for k, v in skill_weights.items()}
    df = skill_document_frequency(role_rows)
    n_roles = len(role_rows)

    for row in role_rows:
        role = str(row.get("role_name") or "").strip()
        if not role:
            continue
        reqs = row.get("skills") or []
        req_names: List[str] = []
        for sk in reqs:
            if isinstance(sk, dict):
                name = str(sk.get("name") or "").strip()
            else:
                name = str(sk).strip()
            if name:
                req_names.append(name)
        if not req_names:
            continue

        slot = _slot(acc, role, category=row.get("category"))
        req_mass = 0.0
        hit_mass = 0.0
        missing: List[str] = []
        confs: List[float] = []

        for name in req_names:
            w_idf = idf_weight(name, df, n_roles)
            req_mass += w_idf
            hit = student.get(name.lower())
            if hit:
                canon, conf = hit
                hit_mass += w_idf * (0.55 + 0.45 * conf)
                confs.append(conf)
                _add_match(slot, canon)
                slot["sources"].add("neo4j_requires")
            else:
                missing.append(name)

        coverage = (hit_mass / req_mass) if req_mass > 0 else 0.0
        # Prefer roles with ≥2 distinct matches; single hub hit (Python) alone is weak
        distinct = len(slot["matched_skills"])
        if distinct < 2 and coverage < 0.55:
            coverage *= 0.45
        mean_conf = sum(confs) / len(confs) if confs else 0.0
        score = coverage * (0.85 + 0.15 * mean_conf) * 3.0  # scale vs secondary signals

        slot["coverage"] = max(slot["coverage"], coverage)
        slot["score"] += score
        slot["missing_core_skills"] = missing
        slot["_hit_mass"] = hit_mass
        slot["_req_mass"] = req_mass

    return acc


def score_insights_coverage(
    acc: Dict[str, Dict[str, Any]],
    insight_rows: Sequence[Dict[str, Any]],
    skill_weights: Dict[str, float],
) -> None:
    """Secondary: coverage vs career_insights.core_skills (+ mild demand prior)."""
    student = {k.lower(): (k, v) for k, v in skill_weights.items()}
    for row in insight_rows or []:
        role = str(row.get("role_name") or "").strip()
        if not role:
            continue
        core = _parse_str_list(row.get("core_skills"))
        if not core:
            continue
        matched: List[str] = []
        missing: List[str] = []
        hit_w = 0.0
        for name in core:
            hit = student.get(name.lower())
            if hit:
                canon, conf = hit
                matched.append(canon)
                hit_w += conf
            else:
                missing.append(name)
        if not matched:
            continue
        coverage = len(matched) / len(core)
        demand = str(row.get("demand_level") or "中")
        prior = _DEMAND_PRIOR.get(demand, 1.0)
        # Honest Career Stance: do not inflate hard-entry roles just because demand is high
        entry = str(row.get("entry_difficulty") or "")
        entry_pen = 0.92 if entry in ("难", "很难", "高") else 1.0
        boost = coverage * (0.6 + 0.4 * (hit_w / max(len(matched), 1))) * 1.35 * prior * entry_pen

        slot = _slot(
            acc,
            role,
            category=row.get("category"),
            demand_level=demand,
        )
        slot["score"] += boost
        slot["coverage"] = max(slot["coverage"], coverage)
        slot["sources"].add("career_insights")
        for m in matched:
            _add_match(slot, m)
        if not slot["missing_core_skills"]:
            slot["missing_core_skills"] = missing


def score_taxonomy_vote(
    acc: Dict[str, Dict[str, Any]],
    taxonomy_rows: Sequence[Dict[str, Any]],
    skill_weights: Dict[str, float],
) -> None:
    """Tertiary / OOV bridge: related_roles vote with heat_score, capped."""
    for row in taxonomy_rows or []:
        skill = str(row.get("skill_name") or "").strip()
        if not skill or skill not in skill_weights:
            continue
        w = float(skill_weights[skill])
        try:
            heat = float(row.get("heat_score") or 0.5)
        except (TypeError, ValueError):
            heat = 0.5
        # Cap so taxonomy cannot override graph coverage
        vote = min(0.55, w * (0.35 + 0.25 * heat))
        for role in _parse_str_list(row.get("related_roles")):
            slot = _slot(acc, role)
            slot["score"] += vote
            slot["sources"].add("skill_taxonomy")
            _add_match(slot, skill)


def apply_preferred_boost(
    acc: Dict[str, Dict[str, Any]],
    preferred_role: Optional[str],
) -> None:
    if not preferred_role or not preferred_role.strip():
        return
    pref = preferred_role.strip()
    # Soft match: exact or substring either way
    targets = [pref]
    for role in list(acc.keys()):
        if pref in role or role in pref:
            targets.append(role)
    for role in dict.fromkeys(targets):
        slot = _slot(acc, role)
        slot["score"] += 0.85
        slot["sources"].add("preferred")


def finalize_candidates(
    acc: Dict[str, Dict[str, Any]],
    *,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for slot in acc.values():
        # Drop noise: taxonomy-only with no coverage and single hub match
        sources = slot.get("sources") or set()
        if isinstance(sources, set):
            sources_list = sorted(sources)
        else:
            sources_list = list(sources)
        if (
            sources_list == ["skill_taxonomy"]
            and float(slot.get("coverage") or 0) < 0.15
            and len(slot.get("matched_skills") or []) < 2
        ):
            continue
        ranked.append(
            {
                "role_name": slot["role_name"],
                "score": round(float(slot["score"]), 4),
                "coverage": round(float(slot.get("coverage") or 0.0), 4),
                "matched_skills": list(slot.get("matched_skills") or []),
                "missing_core_skills": list(slot.get("missing_core_skills") or [])[:12],
                "sources": sources_list,
                "category": slot.get("category"),
                "demand_level": slot.get("demand_level"),
            }
        )
    ranked.sort(key=lambda x: (-x["score"], -x["coverage"], x["role_name"]))
    return ranked[: max(1, min(int(top_k or 5), 20))]


def _confidence_from_ranked(ranked: List[Dict[str, Any]]) -> float:
    if not ranked:
        return 0.0
    top = ranked[0]
    top_score = float(top["score"])
    second = float(ranked[1]["score"]) if len(ranked) > 1 else 0.0
    margin = max(0.0, top_score - second)
    cov = float(top.get("coverage") or 0.0)
    n_match = len(top.get("matched_skills") or [])
    sources = set(top.get("sources") or [])
    graph_bonus = 0.08 if "neo4j_requires" in sources else 0.0
    conf = (
        0.32
        + 0.35 * min(1.0, cov)
        + 0.08 * min(n_match, 4)
        + 0.06 * min(margin, 2.0)
        + graph_bonus
    )
    return round(min(0.95, conf), 4)


def _evidence_summary(ranked: List[Dict[str, Any]], skill_count: int) -> str:
    if not ranked:
        return f"skills={skill_count}; no role candidates"
    top = ranked[0]
    src = ",".join(top.get("sources") or [])
    return (
        f"skills={skill_count}; top={top['role_name']} "
        f"coverage={top.get('coverage')} matched={len(top.get('matched_skills') or [])} "
        f"sources={src}"
    )


def _with_source_coverage(result: Dict[str, Any], source_coverage: Dict[str, str]) -> Dict[str, Any]:
    """Do not represent a dependency outage as a healthy empty inference."""
    failed_sources = [name for name, status in source_coverage.items() if status == "unavailable"]
    result["source_coverage"] = source_coverage
    result["failed_sources"] = failed_sources
    result["status"] = "partial" if failed_sources else ("complete" if result["candidates"] else "empty")
    if failed_sources:
        result["next_action"] = "retry_when_sources_available"
        result["evidence_summary"] += "; partial_sources=" + ",".join(failed_sources)
    return result


def infer_roles_from_skills(
    skills: Sequence[SkillInput],
    *,
    top_k: int = 5,
    preferred_role: Optional[str] = None,
    mysql_client: Any = None,
    neo4j_client: Any = None,
) -> Dict[str, Any]:
    """
    Deep Role Inference from Extracted Skills.

    Order: canonicalize → Neo4j REQUIRES coverage (IDF) → insights coverage
    → taxonomy vote → preferred boost → rank.
    """
    pairs = normalize_skill_inputs(skills)
    source_coverage = {
        "neo4j_requires": "not_queried",
        "career_insights": "not_queried",
        "skill_taxonomy": "not_queried",
    }
    if not pairs:
        return _with_source_coverage(_empty_result(preferred_role, 0), source_coverage)

    if mysql_client is None:
        try:
            from .db import get_mysql_client

            mysql_client = get_mysql_client()
        except Exception as e:
            logger.warning("mysql client unavailable: %s", e)
            mysql_client = None
            source_coverage["career_insights"] = "unavailable"
            source_coverage["skill_taxonomy"] = "unavailable"

    taxonomy_names: List[str] = []
    if mysql_client is not None:
        try:
            rows = mysql_client.execute(
                "SELECT skill_name FROM skill_taxonomy LIMIT 2000", {}
            ) or []
            taxonomy_names = [str(r.get("skill_name") or "") for r in rows if r.get("skill_name")]
            source_coverage["skill_taxonomy"] = "available"
        except Exception as e:
            logger.warning("taxonomy name load failed: %s", e)
            source_coverage["skill_taxonomy"] = "unavailable"

    skill_weights = canonicalize_skills(pairs, taxonomy_names)
    names = list(skill_weights.keys())

    if neo4j_client is None:
        try:
            from .db import get_neo4j_client

            neo4j_client = get_neo4j_client()
        except Exception as e:
            logger.warning("neo4j client unavailable: %s", e)
            neo4j_client = None

    role_rows: List[Dict[str, Any]] = []
    if neo4j_client is not None:
        try:
            role_rows = fetch_neo4j_role_requirements(neo4j_client)
            source_coverage["neo4j_requires"] = "available"
        except Exception:
            source_coverage["neo4j_requires"] = "unavailable"
    else:
        source_coverage["neo4j_requires"] = "unavailable"

    acc = score_neo4j_coverage(role_rows, skill_weights)

    insight_rows: List[Dict[str, Any]] = []
    if mysql_client is not None:
        try:
            insight_rows = mysql_client.execute(
                """
                SELECT role_name, core_skills, category, demand_level, entry_difficulty
                FROM career_insights
                ORDER BY skill_count DESC
                LIMIT 80
                """,
                {},
            ) or []
            source_coverage["career_insights"] = "available"
        except Exception as e:
            logger.warning("career_insights lookup failed: %s", e)
            source_coverage["career_insights"] = "unavailable"

    score_insights_coverage(acc, insight_rows, skill_weights)

    # Taxonomy only for skills we have (exact after canonicalize)
    placeholders = ", ".join(f":s{i}" for i in range(len(names)))
    params = {f"s{i}": n for i, n in enumerate(names)}
    taxonomy_rows: List[Dict[str, Any]] = []
    if mysql_client is not None:
        try:
            taxonomy_rows = mysql_client.execute(
                f"""
                SELECT skill_name, related_roles, category, heat_score
                FROM skill_taxonomy
                WHERE skill_name IN ({placeholders})
                """,
                params,
            ) or []
            if source_coverage["skill_taxonomy"] != "unavailable":
                source_coverage["skill_taxonomy"] = "available"
        except Exception as e:
            logger.warning("skill_taxonomy lookup failed: %s", e)
            source_coverage["skill_taxonomy"] = "unavailable"

    score_taxonomy_vote(acc, taxonomy_rows, skill_weights)
    apply_preferred_boost(acc, preferred_role)

    ranked = finalize_candidates(acc, top_k=top_k)
    if not ranked:
        return _with_source_coverage(_empty_result(
            preferred_role,
            len(names),
            evidence="graph/insights empty after scoring",
        ), source_coverage)

    return _with_source_coverage({
        "predicted_role": ranked[0]["role_name"],
        "confidence": _confidence_from_ranked(ranked),
        "candidates": ranked,
        "skill_count": len(names),
        "canonical_skills": names,
        "evidence_summary": _evidence_summary(ranked, len(names)),
    }, source_coverage)
