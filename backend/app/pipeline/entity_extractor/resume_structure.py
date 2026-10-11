"""
Resume Structure Truth — LLM-canonical student_profile from full raw text.

Architecture (no regex repair):
  1. raw_text must be complete (upload must not silently truncate).
  2. Canonical extract = one LLM call over the full resume when it fits the
     structure budget; otherwise overlapping LLM windows + one LLM merge pass.
  3. Validate required fields on the resulting profile. If incomplete → refuse
     scoring (Career Nav must not paint zeros as “解析完成”).

Career Navigation and Agent must consume the same student_profile shape.
"""

from __future__ import annotations

import json
import logging
import hashlib
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, Field, ValidationError
from typing import Any, Dict, List, Optional, Tuple

from config.prompt_loader import load_prompt
from config.loader import get_rules_config

logger = logging.getLogger(__name__)


class SemanticFact(BaseModel):
    value: StrictStr | StrictBool
    evidence: str = Field(min_length=1)


class _WireObject(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _QuotedFact(_WireObject):
    value: StrictStr | StrictBool
    evidence: str = Field(min_length=1)


class _FactSet(_WireObject):
    school_tier: _QuotedFact | None = None
    major_category: _QuotedFact | None = None
    communication: _QuotedFact | None = None
    teamwork: _QuotedFact | None = None
    stress_tolerance: _QuotedFact | None = None
    responsibility: _QuotedFact | None = None
    internship: _QuotedFact | None = None
    team_project: _QuotedFact | None = None
    leader: _QuotedFact | None = None
    student_officer: _QuotedFact | None = None


class _Project(_WireObject):
    name: str
    role: str | None = None
    desc: str | None = None
    is_team: bool | None = None


class _NamedLevel(_WireObject):
    name: str
    level: str | None = None


class _SkillMention(_WireObject):
    name: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class _CanonicalProfile(_WireObject):
    """Wire contract for the existing normalizer, not a second profile store."""
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    grade: str | None = None
    school: str | None = None
    major: str | None = None
    degree: str | None = None
    target_role: str | None = None
    gpa: float | None = None
    rank_percent: float | None = None
    projects: List[_Project] = Field(default_factory=list)
    competitions: List[_NamedLevel] = Field(default_factory=list)
    certificates: List[str] = Field(default_factory=list)
    research: List[_NamedLevel] = Field(default_factory=list)
    leadership_roles: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    skill_mentions: List[_SkillMention] = Field(default_factory=list)
    internship_months: int | None = None
    semantic_facts: _FactSet = Field(default_factory=_FactSet)


def _canonical_response_format():
    # Same Pydantic -> JSON Schema protocol already used by LLMSkillExtractor.
    # Strict providers require every declared property, with unknown scalars null.
    schema = _CanonicalProfile.model_json_schema()
    # Mentions have a separate optional model call. Quoted evidence must not
    # enlarge the critical canonical-profile output or its reduce operation.
    schema["properties"].pop("skill_mentions", None)
    schema.get("$defs", {}).pop("_SkillMention", None)

    def require_properties(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if "properties" in node:
                node["required"] = list(node["properties"])
            for child in node.values():
                require_properties(child)
        elif isinstance(node, list):
            for child in node:
                require_properties(child)

    require_properties(schema)
    return {"type": "json_schema", "json_schema": {
        "name": "canonical_student_profile", "strict": True, "schema": schema,
    }}


def confirmed_semantic_fact(profile, key):
    """Only consume schema-valid, quoted facts in a confirmed source snapshot."""
    if not (profile.get("_resume_confirmation") or {}).get("task_id"):
        return None
    fact = (profile.get("semantic_facts") or {}).get(key)
    if not isinstance(fact, dict) or fact.get("source_sha256") != profile.get("source_sha256"):
        return None
    if not fact.get("source_sha256"):
        return None
    try:
        return SemanticFact.model_validate(fact).value
    except ValidationError:
        return None


def _ground_semantic_facts(profile, data, text):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    profile["source_sha256"] = digest
    facts = {}
    issues = []
    candidates = data.get("semantic_facts") or {}
    for key, raw in (candidates.items() if isinstance(candidates, dict) else []):
        if raw is None:
            continue
        try:
            fact = SemanticFact.model_validate(raw)
        except (ValidationError, TypeError):
            issues.append({"field": key, "reason": "invalid_fact_schema"})
            continue
        if fact.evidence in text:
            facts[key] = {**fact.model_dump(), "source_sha256": digest}
        else:
            issues.append({"field": key, "reason": "evidence_not_in_source"})
    if facts:
        profile["semantic_facts"] = facts
    if issues:
        profile["semantic_fact_issues"] = issues
        profile["semantic_fact_next_action"] = "retry_fact_extraction"
    return profile


def _semantic_fact_instruction():
    """Expose the existing domain contract, not a new name-to-category heuristic."""
    tables = get_rules_config().scoring_tables
    categories = {key: [value for value in tables[table] if value]
                  for key, table in (("school_tier", "school_tier_score"),
                                     ("major_category", "major_category_score"))}
    return (
        '\n必须输出 semantic_facts 对象；未知项设 null。已知项仅含 value 与 evidence（逐字原文引句）。'
        '嵌套格式必须是 {"semantic_facts":{"字段名":{"value":"一个明确值","evidence":"连续原句"}}}。'
        '已知项不能直接写成字符串或枚举数组；不能把下方候选枚举列表复制为提取结果。'
        '类别枚举：' + json.dumps(categories, ensure_ascii=False) + '。'
        '原文明确出现枚举值时应提取；尤其 major 与 major_category 枚举逐字相同，'
        '可复制该专业全称并引用原句，这不是按名称猜测类别。'
        '不得根据学校名称猜学校层级，不得将普通本科等同一本或二本，'
        '不得将未出现在原文的专业大类当成原文明示。'
        'communication/teamwork/stress_tolerance/responsibility 仅原文有明确强/中/弱自述时填；'
        '行为经历不能直接充当能力等级。'
        'internship/team_project/leader/student_officer 用明确事实布尔值。没有依据则省略该项。'
        '此字段属于上方 student_profile JSON，与 major/projects 同级；不能仅输出普通字段而漏掉它。'
    )


def _merge_grounded_facts(profile, partials, text):
    """Transport validated map atoms; reduce may not invent or resolve conflicting facts."""
    candidates = {}
    conflicts = set()
    for part in partials:
        for key, fact in (part.get("semantic_facts") or {}).items():
            previous = candidates.get(key)
            if previous is not None and previous["value"] != fact["value"]:
                conflicts.add(key)
            else:
                candidates[key] = fact
    _ground_semantic_facts(profile, {"semantic_facts": {
        key: fact for key, fact in candidates.items() if key not in conflicts
    }}, text)
    if conflicts:
        profile["semantic_fact_conflicts"] = sorted(conflicts)
        profile["semantic_fact_next_action"] = "review_conflicting_facts"
    issues = [{**issue, "window_offset": part.get("_window_offset", 0)}
              for part in partials for issue in part.get("semantic_fact_issues", [])]
    if issues:
        profile["semantic_fact_issues"] = issues
        profile.setdefault("semantic_fact_next_action", "retry_fact_extraction")
    return profile

# Character budgets for structure LLM (not silent 1.5k/2k head-slices)
CHARS_FULL_STRUCTURE = 14000
CHARS_CHUNK = 7000
CHARS_CHUNK_OVERLAP = 800
# Includes quoted semantic facts and skill mentions, not merely the old fields.
# LLMClient enforces model context and turn budgets before each request.
STRUCTURE_OUTPUT_TOKENS = 6000

# Window helpers for legacy multi-call NS-LE (still LLM, not regex)
CHARS_BASIC = 4000
CHARS_EDUCATION = 10000
CHARS_AWARDS = 10000
CHARS_EXPERIENCE = 10000
CHARS_SKILLS = 8000


def structure_windows(raw_text: str) -> Dict[str, str]:
    """Slice budgets for section LLM calls — length limits only, no pattern matching."""
    t = (raw_text or "").strip()
    return {
        "basic": t,
        "education": t,
        "awards": t,
        "experience": t,
        "skills": t,
        "full": t,
    }


def _parse_json(content: str) -> Optional[Any]:
    text = (content or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    for i, ch in enumerate(text):
        if ch not in ("{", "["):
            continue
        closing = "}" if ch == "{" else "]"
        for j in range(len(text), i, -1):
            if text[j - 1] != closing:
                continue
            try:
                return json.loads(text[i:j])
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def _normalize_profile(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in (
        "name", "phone", "email", "grade", "school", "major", "degree", "target_role",
    ):
        val = data.get(key)
        if val is not None and str(val).strip():
            out[key] = str(val).strip()

    for key in ("gpa", "rank_percent"):
        raw = data.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            out[key] = float(raw)
        except (TypeError, ValueError):
            pass

    projects: List[Dict[str, Any]] = []
    for p in data.get("projects") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name and not p.get("desc"):
            continue
        projects.append({
            "name": name or "项目",
            "role": str(p.get("role") or "成员").strip(),
            "is_team": bool(p.get("is_team")),
            "desc": str(p.get("desc") or ""),
        })
    if projects:
        out["projects"] = projects

    competitions: List[Dict[str, Any]] = []
    for c in data.get("competitions") or []:
        if isinstance(c, dict) and str(c.get("name") or "").strip():
            level = str(c.get("level") or c.get("award") or "其他").strip()
            competitions.append({
                "name": str(c["name"]).strip(),
                "level": level,
                "award": level,
            })
        elif isinstance(c, str) and c.strip():
            competitions.append({"name": c.strip(), "level": "其他", "award": "其他"})
    if competitions:
        out["competitions"] = competitions

    certs: List[str] = []
    for c in data.get("certificates") or []:
        if isinstance(c, dict) and c.get("name"):
            certs.append(str(c["name"]).strip())
        elif isinstance(c, str) and c.strip():
            certs.append(c.strip())
    if certs:
        out["certificates"] = certs

    research: List[Dict[str, Any]] = []
    for r in data.get("research") or []:
        if isinstance(r, dict) and (r.get("name") or r.get("level")):
            research.append({
                "name": str(r.get("name") or "").strip(),
                "level": str(r.get("level") or "校级").strip(),
            })
    if research:
        out["research"] = research

    leads = [
        str(x).strip()
        for x in (data.get("leadership_roles") or [])
        if str(x).strip()
    ]
    if leads:
        out["leadership_roles"] = list(dict.fromkeys(leads))

    skills = []
    for s in data.get("skills") or []:
        if isinstance(s, str) and s.strip():
            skills.append(s.strip())
        elif isinstance(s, dict) and s.get("name"):
            skills.append(str(s["name"]).strip())
    if skills:
        out["skills"] = skills
    out["skill_mentions"] = data.get("skill_mentions") or []

    months = data.get("internship_months")
    if months is not None and str(months).strip() != "":
        try:
            out["internship_months"] = min(max(int(months), 0), 24)
        except (TypeError, ValueError):
            pass

    out["source"] = "canonical_llm"
    return out


def _chunk_text(text: str) -> List[Tuple[int, str]]:
    """Overlap windows by character index (length geometry only)."""
    t = text or ""
    if not t:
        return []
    if len(t) <= CHARS_FULL_STRUCTURE:
        return [(0, t)]
    out: List[Tuple[int, str]] = []
    i = 0
    while i < len(t):
        out.append((i, t[i:i + CHARS_CHUNK]))
        if i + CHARS_CHUNK >= len(t):
            break
        i += max(CHARS_CHUNK - CHARS_CHUNK_OVERLAP, 1)
    return out


def extract_canonical_profile(llm_client, raw_text: str) -> Dict[str, Any]:
    """
    Authoritative student_profile via LLM. No regex backfill.
    """
    text = (raw_text or "").strip()
    if not text:
        return {"source": "canonical_llm", "raw_text_chars": 0}

    chunks = _chunk_text(text)
    calls = []
    if len(chunks) == 1:
        prompt = load_prompt(
            "pipeline.nsle.extract.student_profile.user",
            text=chunks[0][1],
        )
        prompt += _semantic_fact_instruction()
        try:
            resp = _structure_call(llm_client, calls, stage="extract", input_chars=len(text),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=STRUCTURE_OUTPUT_TOKENS,
                response_format=_canonical_response_format(),
            ) or ""
            data = _parse_json(resp)
            if not isinstance(data, dict):
                raise ValueError("invalid_profile_schema")
            data = _CanonicalProfile.model_validate(data).model_dump(exclude_none=True)
            profile = _normalize_profile(data)
            _ground_semantic_facts(profile, data, text)
            profile["raw_text_chars"] = len(text)
            profile["structure_path"] = "single_shot"
            profile["structure_calls"] = calls
            _link_profile_entities(profile, llm_client, text)
            return profile
        except Exception as e:
            logger.error("canonical single-shot extract failed: %s", e)
            return {"source": "canonical_llm", "raw_text_chars": len(text), "error": str(e),
                    "structure_calls": calls, "next_action": "retry_extraction"}

    # Map: per-window LLM extract
    partials: List[Dict[str, Any]] = []
    failed_offsets = []
    for offset, chunk in chunks:
        prompt = load_prompt(
            "pipeline.nsle.extract.student_profile.user",
            text=chunk,
        )
        prompt += _semantic_fact_instruction()
        try:
            resp = _structure_call(llm_client, calls, stage="map", input_chars=len(chunk),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=STRUCTURE_OUTPUT_TOKENS,
                response_format=_canonical_response_format(),
            ) or ""
            decoded = _parse_json(resp)
            if not isinstance(decoded, dict):
                raise ValueError("invalid_profile_schema")
            decoded = _CanonicalProfile.model_validate(decoded).model_dump(exclude_none=True)
            part = _normalize_profile(decoded)
            _ground_semantic_facts(part, decoded, chunk)
            if part:
                part["_window_offset"] = offset
                partials.append(part)
        except Exception as e:
            failed_offsets.append(offset)
            logger.warning("canonical chunk extract offset=%s failed: %s", offset, e)

    if not partials:
        return {
            "source": "canonical_llm",
            "raw_text_chars": len(text),
            "structure_path": "chunked_empty",
            "error": "all_chunk_extracts_failed",
            "structure_calls": calls,
        }

    # Reduce: LLM merge into one canonical profile
    merge_prompt = load_prompt(
        "pipeline.nsle.merge.student_profile.user",
        partials_json=json.dumps(partials, ensure_ascii=False, indent=2),
    )
    try:
        resp = _structure_call(llm_client, calls, stage="reduce", input_chars=len(merge_prompt),
            messages=[{"role": "user", "content": merge_prompt}],
            temperature=0.1,
            max_tokens=STRUCTURE_OUTPUT_TOKENS,
            response_format=_canonical_response_format(),
        ) or ""
        decoded = _parse_json(resp)
        if not isinstance(decoded, dict):
            raise ValueError("invalid_profile_schema")
        decoded = _CanonicalProfile.model_validate(decoded).model_dump(exclude_none=True)
        profile = _normalize_profile(decoded)
        _merge_grounded_facts(profile, partials, text)
        profile["raw_text_chars"] = len(text)
        profile["structure_path"] = "chunk_merge"
        profile["chunk_count"] = len(chunks)
        profile["structure_calls"] = calls
        # Preserve grounded map mentions rather than letting reduce erase them.
        profile["skill_mentions"] = list({(m["name"], m["evidence"]): m
            for part in partials for m in part.get("skill_mentions", [])
            if m.get("evidence") and m["evidence"] in text}.values())
        _link_profile_entities(profile, llm_client, text)
        if failed_offsets:
            profile["error"] = "partial_chunk_extraction"
            profile["failed_offsets"] = failed_offsets
            profile["next_action"] = "retry_extraction"
        return profile
    except Exception as e:
        logger.error("canonical merge failed: %s", e)
        # Last resort: first non-empty partial (still LLM-derived, not regex)
        base = dict(partials[0])
        base.pop("_window_offset", None)
        _merge_grounded_facts(base, partials[:1], text)
        base["raw_text_chars"] = len(text)
        base["structure_path"] = "chunk_partial_fallback"
        base["error"] = f"merge_failed:{e}"
        base["structure_calls"] = calls
        base["next_action"] = "retry_extraction"
        return base


def _structure_call(client, calls, *, stage, input_chars, **kwargs):
    record = {"stage": stage, "input_chars": input_chars,
              "requested_output_tokens": kwargs["max_tokens"], "status": "failed",
              "usage": None, "finish_reason": None}
    calls.append(record)
    try:
        method = getattr(client, "chat_with_metadata", None)
        if callable(method):
            response = method(**kwargs)
            record.update(usage=response.usage, finish_reason=response.finish_reason,
                          provider=response.provider, model=response.model)
            value = response.text
        else:
            value = client.chat(**kwargs)
        record["status"] = "completed"
        return value
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        raise


def _link_profile_entities(profile, llm_client, text):
    from app.services.resume_entity_linking import link_resume_mentions
    # Entity enrichment cannot invalidate a usable canonical resume. Reuse the
    # existing evidence-backed extractor and its no-truncation/budget contract.
    # No head-slice, implicit keyword fallback, or provider retry is introduced.
    if not profile.get("skill_mentions") and profile.get("skills"):
        from .llm_skill_extractor import LLMSkillExtractor
        extractor = LLMSkillExtractor()
        extractor._llm_client = llm_client
        try:
            extracted = extractor.extract_mentions(text)
            profile["skill_mentions"] = extracted["mentions"]
            profile["entity_extraction_call"] = extracted["call"]
            profile["entity_extraction_status"] = extracted["status"]
            if extracted["issues"]:
                profile["entity_extraction_issues"] = extracted["issues"]
        except Exception as exc:
            profile["entity_linking"] = {"status": "unresolved", "reason": "mention_extraction_unavailable",
                "error_type": type(exc).__name__,
                "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "mentions": [], "candidates": [], "decisions": [], "next_action": "retry_entity_linking"}
            return
    profile["entity_linking"] = link_resume_mentions(
        llm_client, profile.get("skill_mentions", []), text)
    if profile.get("entity_extraction_status") == "partial":
        profile["entity_linking"]["extraction_partial"] = True
        profile["entity_linking"]["next_action"] = "retry_entity_extraction"


def validate_structure_truth(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Field-presence validation after LLM extract.
    """
    p = profile or {}
    school = str(p.get("school") or "").strip()
    major = str(p.get("major") or "").strip()
    comps = p.get("competitions") if isinstance(p.get("competitions"), list) else []
    projects = p.get("projects") if isinstance(p.get("projects"), list) else []
    issues: List[str] = []
    if p.get("error"):
        issues.append("extraction_incomplete")
    if not school:
        issues.append("missing_school")
    if not major:
        issues.append("missing_major")
    if not comps and not projects:
        issues.append("missing_competitions_and_projects")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "school": school,
        "major": major,
        "competition_count": len(comps),
        "project_count": len(projects),
        "raw_text_chars": int(p.get("raw_text_chars") or 0),
        "structure_path": p.get("structure_path"),
    }
