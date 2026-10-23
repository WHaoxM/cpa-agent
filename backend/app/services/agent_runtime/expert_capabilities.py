"""Server-owned bindings for explicitly selected durable Expert capabilities.

Capability identifiers are stable API identities. The versioned manifest is
separate: it is pinned into each plan release so a queued run cannot silently
inherit a newer capability definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence


class CapabilitySelectionError(ValueError):
    """The explicit expert/capability pair is not a supported selection."""


class CapabilityPlanMismatch(ValueError):
    """A plan does not preserve the selected capability's server binding."""


@dataclass(frozen=True)
class ExpertCapability:
    expert_id: str
    capability_id: str
    version: str
    name: str
    role: str
    skill_ids: tuple[str, ...]
    max_steps: int = 6
    execution_pipeline: Optional[str] = None

    def manifest(self) -> Dict[str, Any]:
        """Return the small immutable contract persisted with a run plan."""
        manifest = {
            "expert_id": self.expert_id,
            "capability_id": self.capability_id,
            "version": self.version,
            "name": self.name,
            "role": self.role,
            "skill_ids": list(self.skill_ids),
            "max_steps": self.max_steps,
            "read_only": True,
        }
        if self.execution_pipeline:
            manifest["execution_pipeline"] = self.execution_pipeline
        return manifest

    def plan_step(self) -> Dict[str, Any]:
        """Build the only plan step this read-only capability is allowed to run."""
        return {
            "role": self.role,
            "goal": (
                "只读回答用户的职业规划咨询；可结合服务端读取的已确认院校背景事实，"
                "不得写入画像、分数、匹配、报告或学习计划。若用户要求需正式业务写入、"
                "生成其他专家成果或超出本能力范围，明确说明当前能力仅提供只读答疑，"
                "并请用户切换普通 Agent 或适用的能力包；不得声称已执行。"
            ),
            "skill_ids": list(self.skill_ids),
        }

    def validate_plan(self, steps: Sequence[Mapping[str, Any]]) -> None:
        """Reject rather than repair a plan that changed or dropped the binding."""
        if not 1 <= len(steps) <= self.max_steps:
            raise CapabilityPlanMismatch("selected_capability_plan_step_count_invalid")
        for index, step in enumerate(steps, start=1):
            actual_role = str(step.get("role") or "")
            actual_skills = step.get("skill_ids")
            if actual_role != self.role or actual_skills != list(self.skill_ids):
                raise CapabilityPlanMismatch(
                    f"selected_capability_role_or_skill_mismatch_step_{index}"
                )


# The public capability_id is intentionally not versioned. Changes to its
# behavior require a new package version, which is pinned in the release.
_CAPABILITIES: Dict[tuple[str, str], ExpertCapability] = {
    ('task-coordinator', 'student-task'): ExpertCapability(
        expert_id='task-coordinator', capability_id='student-task', version='1.0.0',
        name='学生任务陪伴', role='qa', skill_ids=(), max_steps=1,
        execution_pipeline='student_task_v1',
    ),
    ("career-coach", "career-exploration"): ExpertCapability(
        expert_id="career-coach", capability_id="career-exploration", version="1.0.0",
        name="职业探索陪伴", role="qa", skill_ids=(), max_steps=1,
        execution_pipeline="career_exploration_companion_v1",
    ),
    ("career-coach", "career-planning"): ExpertCapability(
        expert_id="career-coach",
        capability_id="career-planning",
        version="1.0.0",
        name="职业规划顾问",
        role="qa",
        skill_ids=("school-context",),
    ),
    ("career-coach", "education-screening"): ExpertCapability(
        expert_id="career-coach",
        capability_id="education-screening",
        version="0.1.0",
        name="学历与院校背景咨询（试点）",
        role="qa",
        skill_ids=("education-screening-context", "school-context"),
        max_steps=1,
        execution_pipeline="education_screening_v1",
    ),
    ("career-coach", "education-screening-structured"): ExpertCapability(
        expert_id="career-coach",
        capability_id="education-screening-structured",
        version="0.1.0",
        name="教育筛选与院校背景咨询（结构化试点）",
        role="qa",
        skill_ids=("education-screening-context", "school-context"),
        max_steps=1,
        execution_pipeline="education_screening_structured_v2",
    ),
    ("career-coach", "education-screening-structured-v3"): ExpertCapability(
        expert_id="career-coach",
        capability_id="education-screening-structured-v3",
        version="0.1.0",
        name="教育筛选与院校背景咨询（结构化试验 v3）",
        role="qa",
        skill_ids=("education-screening-context", "school-context"),
        max_steps=1,
        execution_pipeline="education_screening_structured_v3",
    ),
    ("career-coach", "education-screening-structured-v4"): ExpertCapability(
        expert_id="career-coach",
        capability_id="education-screening-structured-v4",
        version="0.1.0",
        name="教育筛选与院校背景咨询（受限结论试验 v4）",
        role="qa",
        skill_ids=("education-screening-context", "school-context"),
        max_steps=1,
        execution_pipeline="education_screening_structured_v4",
    ),
    ("career-coach", "education-screening-structured-v5"): ExpertCapability(
        expert_id="career-coach",
        capability_id="education-screening-structured-v5",
        version="0.1.0",
        name="教育筛选与院校背景咨询（服务端措辞试验 v5）",
        role="qa",
        skill_ids=("education-screening-context", "school-context"),
        max_steps=1,
        execution_pipeline="education_screening_structured_v5",
    ),
}


def resolve_explicit_capability(
    selection: Mapping[str, Any],
) -> Optional[ExpertCapability]:
    """Resolve a request selection; no pair means legacy/unselected behavior."""
    expert_id = selection.get("expert_id")
    capability_id = selection.get("capability_id")
    if expert_id is None and capability_id is None:
        return None
    if expert_id is None or capability_id is None:
        raise CapabilitySelectionError("expert_id and capability_id must be supplied together")
    if selection.get("interaction_mode", "agent") != "experts":
        raise CapabilitySelectionError(
            "an explicit Expert capability requires interaction_mode=experts"
        )
    capability = _CAPABILITIES.get((str(expert_id), str(capability_id)))
    if capability is None:
        raise CapabilitySelectionError("expert_id/capability_id is not in the server allowlist")
    return capability


def capability_cards() -> List[Dict[str, Any]]:
    """Expose metadata only; capability binding never exposes Skill bodies."""
    return [item.manifest() for _, item in sorted(_CAPABILITIES.items())]
