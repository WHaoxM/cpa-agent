"""Repository-bundled Agent Skills with on-demand body loading.

Only a small metadata card is exposed to the supervisor. Full instructions are
loaded for a validated plan step, and the returned digest can be recorded with
that step. Skill content is treated as untrusted guidance, not executable code.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_SKILL_ROOT = (_BACKEND_ROOT / "config" / "agent_skills").resolve()
_SKILLS = {
    "school-context": {
        "name": "院校背景解读",
        "description": "依据已确认的院校事实与来源版本，给出有边界的求职语境解释；只读，不修改 D1 评分或正式画像。",
        "path": "school-context/SKILL.md",
        "version": "1.0.2",
        "roles": frozenset({"qa"}),
    },
    "education-screening-context": {
        "name": "中国招聘教育条件分析（候选）",
        "description": "Use when a career decision depends on education credentials, school background, or qualifications in a specific recruitment notice.",
        "path": "education-screening-context/SKILL.md",
        "version": "0.3.0",
        "roles": frozenset({"qa"}),
        "capability_ids": frozenset({
            "education-screening", "education-screening-structured",
            "education-screening-structured-v3", "education-screening-structured-v4",
        }),
        "references": {
            "terms": {
                "path": "education-screening-context/references/terms.md",
                "version": "candidate-2026.10.19",
                "capability_ids": frozenset({
                    "education-screening-structured", "education-screening-structured-v3",
                    "education-screening-structured-v4",
                }),
            },
            "evidence-scoping": {
                "path": "education-screening-context/references/evidence-scoping.md",
                "version": "candidate-2026.10.19",
                "capability_ids": frozenset({
                    "education-screening-structured", "education-screening-structured-v3",
                    "education-screening-structured-v4",
                }),
            },
        },
    },
}


@dataclass(frozen=True)
class LoadedSkill:
    skill_id: str
    name: str
    version: str
    sha256: str
    body: str


@dataclass(frozen=True)
class LoadedSkillReference:
    skill_id: str
    reference_id: str
    version: str
    sha256: str
    body: str


class SkillLoadError(ValueError):
    """A requested skill is unknown, unavailable, or outside its role scope."""


def skill_cards(
    role: Optional[str] = None,
    *,
    capability_id: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Return compact skill metadata; never include the SKILL.md body."""
    cards: List[Dict[str, str]] = []
    for skill_id, spec in sorted(_SKILLS.items()):
        if role and role not in spec["roles"]:
            continue
        scopes = spec.get("capability_ids")
        if scopes and capability_id not in scopes:
            continue
        cards.append({
            "id": skill_id,
            "name": str(spec["name"]),
            "description": str(spec["description"]),
            "version": str(spec["version"]),
            "roles": ",".join(sorted(spec["roles"])),
        })
    return cards


def allowed_skill_ids(
    role: str,
    *,
    capability_id: Optional[str] = None,
) -> frozenset[str]:
    return frozenset(
        skill_id for skill_id, spec in _SKILLS.items()
        if role in spec["roles"]
        and (not spec.get("capability_ids") or capability_id in spec["capability_ids"])
    )


def load_skill(
    skill_id: str,
    *,
    role: str,
    capability_id: Optional[str] = None,
) -> LoadedSkill:
    """Load one allowlisted skill body and calculate its content fingerprint."""
    spec = _SKILLS.get(str(skill_id or ""))
    if (not spec or role not in spec["roles"]
            or (spec.get("capability_ids")
                and capability_id not in spec["capability_ids"])):
        raise SkillLoadError("skill is not allowed for this role")
    path = (_SKILL_ROOT / str(spec["path"])).resolve()
    if _SKILL_ROOT not in path.parents or path.suffix.lower() != ".md":
        raise SkillLoadError("skill path is outside the configured root")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoadError("skill file is unavailable") from exc
    frontmatter, body = _parse_skill_document(content)
    metadata = frontmatter.get("metadata")
    if (frontmatter.get("name") != skill_id
            or frontmatter.get("description") != spec["description"]
            or not isinstance(metadata, dict)
            or metadata.get("version") != spec["version"]):
        raise SkillLoadError("skill metadata differs from the released registry")
    if not body:
        raise SkillLoadError("skill has no instruction body")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return LoadedSkill(
        skill_id=skill_id,
        name=str(spec["name"]),
        version=str(spec["version"]),
        sha256=digest,
        body=body,
    )


def load_skill_reference(
    skill_id: str,
    reference_id: str,
    *,
    role: str,
    capability_id: Optional[str] = None,
) -> LoadedSkillReference:
    """Load one explicitly allowlisted reference; never accept a caller path."""
    spec = _SKILLS.get(str(skill_id or ""))
    if (not spec or role not in spec["roles"]
            or (spec.get("capability_ids")
                and capability_id not in spec["capability_ids"])):
        raise SkillLoadError("skill reference is not allowed for this role")
    references = spec.get("references")
    reference = references.get(str(reference_id or "")) if isinstance(references, dict) else None
    if (not isinstance(reference, dict)
            or capability_id not in reference.get("capability_ids", ())):
        raise SkillLoadError("skill reference is not allowlisted for this capability")
    path = (_SKILL_ROOT / str(reference["path"])).resolve()
    if _SKILL_ROOT not in path.parents or path.suffix.lower() != ".md":
        raise SkillLoadError("skill reference path is outside the configured root")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoadError("skill reference is unavailable") from exc
    body = content.strip()
    if not body:
        raise SkillLoadError("skill reference is empty")
    return LoadedSkillReference(
        skill_id=str(skill_id),
        reference_id=str(reference_id),
        version=str(reference["version"]),
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        body=body,
    )


def _parse_skill_document(content: str) -> tuple[Dict[str, object], str]:
    text = content.lstrip("\ufeff")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", text, re.DOTALL)
    if match is None:
        raise SkillLoadError("SKILL.md must have Agent Skills frontmatter")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillLoadError("skill frontmatter is invalid") from exc
    if not isinstance(metadata, dict):
        raise SkillLoadError("skill frontmatter must be a mapping")
    return metadata, match.group(2).strip()
