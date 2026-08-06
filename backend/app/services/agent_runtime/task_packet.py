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
    plan_step_id: Optional[str] = None
    lean: bool = False  # plan step: omit utterance/criteria noise when True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.subagent not in SUBAGENTS:
            raise BlackboardError(f"unknown subagent: {self.subagent}")
        if not (self.goal or "").strip():
            raise BlackboardError("TaskPacket.goal is required")
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


def build_standard_packet(
    subagent: str,
    goal: str,
    user_utterance: str = "",
    success_criteria: str = "",
    read_keys: Optional[List[str]] = None,
    write_keys: Optional[List[str]] = None,
    plan_step_id: Optional[str] = None,
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
        plan_step_id=plan_step_id,
        lean=True,
    )
    packet.validate()
    return packet
