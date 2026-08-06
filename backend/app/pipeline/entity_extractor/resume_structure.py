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
from typing import Any, Dict, List, Optional, Tuple

from config.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

# Character budgets for structure LLM (not silent 1.5k/2k head-slices)
CHARS_FULL_STRUCTURE = 14000
CHARS_CHUNK = 7000
CHARS_CHUNK_OVERLAP = 800

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
        "basic": t[:CHARS_BASIC],
        "education": t[:CHARS_EDUCATION],
        "awards": t[:CHARS_AWARDS],
        "experience": t[:CHARS_EXPERIENCE],
        "skills": t[:CHARS_SKILLS],
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
            "desc": str(p.get("desc") or "")[:400],
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
    if len(chunks) == 1:
        prompt = load_prompt(
            "pipeline.nsle.extract.student_profile.user",
            text=chunks[0][1],
        )
        try:
            resp = llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2500,
            ) or ""
            data = _parse_json(resp)
            profile = _normalize_profile(data)
            profile["raw_text_chars"] = len(text)
            profile["structure_path"] = "single_shot"
            return profile
        except Exception as e:
            logger.error("canonical single-shot extract failed: %s", e)
            return {"source": "canonical_llm", "raw_text_chars": len(text), "error": str(e)}

    # Map: per-window LLM extract
    partials: List[Dict[str, Any]] = []
    for offset, chunk in chunks:
        prompt = load_prompt(
            "pipeline.nsle.extract.student_profile.user",
            text=chunk,
        )
        try:
            resp = llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            ) or ""
            part = _normalize_profile(_parse_json(resp))
            if part:
                part["_window_offset"] = offset
                partials.append(part)
        except Exception as e:
            logger.warning("canonical chunk extract offset=%s failed: %s", offset, e)

    if not partials:
        return {
            "source": "canonical_llm",
            "raw_text_chars": len(text),
            "structure_path": "chunked_empty",
            "error": "all_chunk_extracts_failed",
        }

    # Reduce: LLM merge into one canonical profile
    merge_prompt = load_prompt(
        "pipeline.nsle.merge.student_profile.user",
        partials_json=json.dumps(partials, ensure_ascii=False, indent=2),
    )
    try:
        resp = llm_client.chat(
            messages=[{"role": "user", "content": merge_prompt}],
            temperature=0.1,
            max_tokens=2500,
        ) or ""
        profile = _normalize_profile(_parse_json(resp))
        profile["raw_text_chars"] = len(text)
        profile["structure_path"] = "chunk_merge"
        profile["chunk_count"] = len(chunks)
        return profile
    except Exception as e:
        logger.error("canonical merge failed: %s", e)
        # Last resort: first non-empty partial (still LLM-derived, not regex)
        base = dict(partials[0])
        base.pop("_window_offset", None)
        base["raw_text_chars"] = len(text)
        base["structure_path"] = "chunk_partial_fallback"
        base["error"] = f"merge_failed:{e}"
        return base


def validate_structure_truth(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Field-presence validation after LLM extract.
    Does not scan raw_text with patterns — incomplete LLM output ⇒ refuse scoring.
    """
    p = profile or {}
    school = str(p.get("school") or "").strip()
    major = str(p.get("major") or "").strip()
    comps = p.get("competitions") if isinstance(p.get("competitions"), list) else []
    projects = p.get("projects") if isinstance(p.get("projects"), list) else []
    issues: List[str] = []
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
