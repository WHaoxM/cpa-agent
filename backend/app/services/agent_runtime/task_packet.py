"""TaskPacket schemas: standard (single-step) and lean (plan step)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .blackboard import BLACKBOARD_KEYS, BlackboardError, default_read_keys, default_write_keys

SUBAGENTS = frozenset({"profile", "match", "report", "planner", "qa"})


@dataclass
class TaskPacket:
    goal: str
    subagent: str
    blackboard_refs: List[str] = field(default_factory=list)
    constraints_refs: List[str] = field(default_factory=list)
    user_utterance: str = ""
    success_criteria: str = ""
    read_keys: List[str] = field(default_factory=list)
    write_keys: List[str] = field(default_factory=list)
    skill_ids: List[str] = field(default_factory=list)
    capability_id: Optional[str] = None
    plan_step_id: Optional[str] = None
    lean: bool = False  # plan step: omit utterance/criteria noise when True
    business_phase: Optional[str] = None  # server-bound output protocol, never a permission grant
    student_discussion: Optional[List[str]] = None  # immutable owner/session inputs; never browser-supplied history

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        if self.capability_id is None:
            value.pop("capability_id", None)
        if self.business_phase is None:
            value.pop("business_phase", None)
        if self.student_discussion is None:
            value.pop('student_discussion', None)
        return value

    def validate(self) -> None:
        if self.subagent not in SUBAGENTS:
            raise BlackboardError(f"unknown subagent: {self.subagent}")
        if not (self.goal or "").strip():
            raise BlackboardError("TaskPacket.goal is required")
        if self.student_discussion is not None and (
            self.capability_id != 'student-task-resume' or not isinstance(self.student_discussion, list)
            or len(self.student_discussion) > 256
            or any(not isinstance(m, str) or not m.strip() for m in self.student_discussion)):
            raise BlackboardError('student discussion is only allowed for the server-bound resume task')
        if self.business_phase is not None and not (
            self.capability_id == 'career-exploration' and self.business_phase == 'practice_review'
        ):
            raise BlackboardError('business phase is not allowed for this capability')
        for key in self.read_keys + self.write_keys + self.blackboard_refs:
            if key not in BLACKBOARD_KEYS:
                raise BlackboardError(f"TaskPacket key not in whitelist: {key}")
        # write_keys must be writable by this subagent
        allowed_writes = set(default_write_keys(self.subagent))
        for key in self.write_keys:
            if key not in allowed_writes:
                raise BlackboardError(
                    f"subagent '{self.subagent}' cannot request write_keys={key}"
                )
        from .skill_registry import allowed_skill_ids
        allowed_skills = allowed_skill_ids(
            self.subagent, capability_id=self.capability_id,
        )
        for skill_id in self.skill_ids:
            if skill_id not in allowed_skills:
                raise BlackboardError(
                    f"skill '{skill_id}' is not allowed for subagent '{self.subagent}'"
                )


def build_standard_packet(
    subagent: str,
    goal: str,
    user_utterance: str = "",
    success_criteria: str = "",
    read_keys: Optional[List[str]] = None,
    write_keys: Optional[List[str]] = None,
    plan_step_id: Optional[str] = None,
    skill_ids: Optional[List[str]] = None,
    capability_id: Optional[str] = None,
) -> TaskPacket:
    rk = list(read_keys) if read_keys is not None else default_read_keys(subagent)
    wk = list(write_keys) if write_keys is not None else default_write_keys(subagent)
    packet = TaskPacket(
        goal=goal,
        subagent=subagent,
        blackboard_refs=list(rk),
        constraints_refs=["constraints.user"] if "constraints.user" in rk else [],
        user_utterance=user_utterance,
        success_criteria=success_criteria or f"完成与 {subagent} 相关的用户目标",
        read_keys=rk,
        write_keys=wk,
        skill_ids=list(skill_ids or []),
        capability_id=capability_id,
        plan_step_id=plan_step_id,
        lean=False,
    )
    packet.validate()
    return packet


def build_lean_packet(
    subagent: str,
    goal: str,
    plan_step_id: str,
    read_keys: Optional[List[str]] = None,
    write_keys: Optional[List[str]] = None,
    skill_ids: Optional[List[str]] = None,
    capability_id: Optional[str] = None,
) -> TaskPacket:
    rk = list(read_keys) if read_keys is not None else default_read_keys(subagent)
    wk = list(write_keys) if write_keys is not None else default_write_keys(subagent)
    packet = TaskPacket(
        goal=goal,
        subagent=subagent,
        blackboard_refs=list(rk),
        constraints_refs=["constraints.user"] if "constraints.user" in rk else [],
        user_utterance="",
        success_criteria="",
        read_keys=rk,
        write_keys=wk,
        skill_ids=list(skill_ids or []),
        capability_id=capability_id,
        plan_step_id=plan_step_id,
        lean=True,
    )
    packet.validate()
    return packet
