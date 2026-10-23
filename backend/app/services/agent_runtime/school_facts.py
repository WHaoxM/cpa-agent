"""Read-only, exact-name school-label facts, separate from Skill instructions.

The bundled 211 list is a historical official list, not an alias resolver or a
current-school identity service. A miss stays unknown; it never means "not 211".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict


_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "config" / "agent_skills" / "school-context" / "references" / "moe-211-2005.json"
)
_EXPECTED_SOURCE_ID = "moe-211-schools-2005"
_EXPECTED_LABEL = "211"


class SchoolFactSourceError(RuntimeError):
    """The bundled fact source is absent or fails its integrity contract."""


def _load_source() -> tuple[Dict[str, Any], str]:
    try:
        raw = _SOURCE.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchoolFactSourceError("school fact source is unavailable") from exc
    if not isinstance(value, dict):
        raise SchoolFactSourceError("school fact source has invalid shape")
    names = value.get("schools")
    if (
        value.get("source_id") != _EXPECTED_SOURCE_ID
        or value.get("title") != "“211工程”学校名单"
        or value.get("publisher") != "中华人民共和国教育部"
        or value.get("label") != _EXPECTED_LABEL
        or not isinstance(names, list)
        or len(names) != 112
        or value.get("expected_count") != len(names)
        or any(not isinstance(name, str) or not name.strip() for name in names)
        or len(set(names)) != len(names)
        or not str(value.get("url") or "").startswith("https://www.moe.gov.cn/")
        or not isinstance(value.get("retrieved_html_sha256"), str)
        or len(value["retrieved_html_sha256"]) != 64
    ):
        raise SchoolFactSourceError("school fact source failed integrity checks")
    return value, hashlib.sha256(raw).hexdigest()


def lookup_historical_211(school_name: str | None) -> Dict[str, Any]:
    """Return a positive exact-name fact or unknown, with source identity.

    Input must come from a separately owner-checked confirmed profile before
    this result can be described as a fact about the current student. This
    function itself never reads or changes a student's business records.
    """
    source, data_sha256 = _load_source()
    original = school_name if isinstance(school_name, str) else ""
    candidate = original.strip()
    exact = bool(candidate and len(candidate) <= 200 and candidate in source["schools"])
    return {
        "school_original": original,
        "matched_name": candidate if exact else None,
        "match_basis": "exact" if exact else "unknown",
        "label": "211",
        "state": "yes" if exact else "unknown",
        "source_id": source["source_id"],
        "source_title": source["title"],
        "source_publisher": source["publisher"],
        "source_url": source["url"],
        "source_document_date": source["source_document_date"],
        "source_html_sha256": source["retrieved_html_sha256"],
        "source_data_sha256": data_sha256,
        "coverage": source["coverage"],
        "scoring_effect": "none",
    }


def read_owner_school_context(mysql, *, student_id: str | None) -> Dict[str, Any]:
    """Read only the server-bound owner's confirmed school and one fact source.

    The caller must supply the Principal's student ID, never a model- or
    browser-provided owner. The confirmation repository remains the authority
    for student facts; this source can establish only a historical 211 match.
    """
    base: Dict[str, Any] = {
        "status": "profile_unverified",
        "profile_assurance": "none",
        "profile_version": None,
        "knowledge_version": None,
        "school_original": None,
        "degree": None,
        "major": None,
        "institution_facts": [],
        "other_labels": {"985": "not_evaluated", "double_first_class": "not_evaluated"},
        "scoring_effect": "none",
        "next_action": "confirm_resume",
    }
    if not isinstance(student_id, str) or not student_id.strip():
        return base

    from ..pipeline import require_confirmed_profile

    try:
        profile = require_confirmed_profile(mysql, student_id)
    except ValueError:
        return base
    confirmation = profile["_resume_confirmation"]
    clipped_fields = []

    def bounded(field: str, limit: int = 200) -> str | None:
        raw = profile.get(field)
        if not isinstance(raw, str):
            return None
        if len(raw) <= limit:
            return raw
        clipped_fields.append(field)
        return raw[:limit] + "…【字段预览已截断，请重新读取已确认资料】"

    base.update({
        "profile_version": confirmation["task_id"],
        "profile_assurance": "user_confirmed_resume_not_enrollment_verified",
        "school_original": bounded("school"),
        "degree": bounded("degree"),
        "major": bounded("major"),
        "profile_fields_truncated": clipped_fields,
    })
    try:
        fact = lookup_historical_211(profile.get("school"))
    except SchoolFactSourceError:
        base.update({"status": "knowledge_unavailable", "next_action": "retry_school_source_read"})
        return base
    base.update({
        "status": "matched_official_name" if fact["state"] == "yes" else "school_label_unknown",
        "knowledge_version": fact["source_id"],
        "institution_facts": [fact],
        "next_action": None if fact["state"] == "yes" else "verify_school_identity_or_source_coverage",
    })
    fact["school_original"] = base["school_original"]
    if clipped_fields:
        base["next_action"] = "read_confirmed_profile_full_field"
    return base


def project_verified_school_facts(context: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Project only bounded, source-backed school facts for the finalizer.

    This intentionally excludes model prose, document numbers, current
    enrollment, 985/double-first-class determinations, and hiring outcomes.
    An exact-list miss stays unknown rather than becoming a negative fact.
    """
    if not isinstance(context, dict) or context.get("status") not in {
        "matched_official_name", "school_label_unknown",
    }:
        return []
    facts = context.get("institution_facts")
    if not isinstance(facts, list):
        return []
    projected: list[Dict[str, Any]] = []
    for fact in facts[:2]:
        if not isinstance(fact, dict):
            continue
        state = fact.get("state")
        match_basis = fact.get("match_basis")
        school_name = fact.get("school_original")
        data_sha256 = str(fact.get("source_data_sha256") or "")
        if (
            fact.get("source_id") != _EXPECTED_SOURCE_ID
            or fact.get("label") != _EXPECTED_LABEL
            or not isinstance(school_name, str)
            or not school_name.strip()
            or not str(fact.get("source_url") or "").startswith("https://www.moe.gov.cn/")
            or len(data_sha256) != 64
            or state not in {"yes", "unknown"}
        ):
            continue
        if state == "yes" and (
            match_basis != "exact"
            or fact.get("matched_name") != school_name.strip()
        ):
            continue
        if state == "unknown" and (
            match_basis != "unknown" or fact.get("matched_name") is not None
        ):
            continue
        projected.append({
            "kind": "historical_school_label",
            "school_original": school_name[:200],
            "matched_name": fact.get("matched_name") if state == "yes" else None,
            "match_basis": match_basis,
            "label": "211",
            "state": state,
            "source_id": _EXPECTED_SOURCE_ID,
            "source_title": str(fact.get("source_title") or "")[:160],
            "source_publisher": str(fact.get("source_publisher") or "")[:100],
            "source_url": str(fact.get("source_url") or "")[:500],
            "document_date": str(fact.get("source_document_date") or "")[:10],
            "knowledge_sha256": data_sha256,
            "profile_version": str(context.get("profile_version") or "")[:100],
            "profile_assurance": str(context.get("profile_assurance") or "")[:100],
            "coverage": str(fact.get("coverage") or "")[:300],
            "other_labels": {"985": "not_evaluated", "double_first_class": "not_evaluated"},
            "scoring_effect": "none",
        })
    return projected
