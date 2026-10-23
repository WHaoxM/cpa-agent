"""Plan whitelist + step-level blackboard commit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .blackboard import Blackboard, BlackboardError
from .task_packet import SUBAGENTS, TaskPacket, build_lean_packet, build_standard_packet
from .skill_registry import allowed_skill_ids

# Whitelisted plan templates (plan D)
PLAN_TEMPLATES: Dict[str, List[str]] = {
    "end_to_end": ["profile", "match", "report"],
    "profile_match": ["profile", "match"],
    "match_report": ["match", "report"],
    "match_planner": ["match", "planner"],
}
MAX_PLAN_STEPS = 6
MAX_DISTINCT_ROLES = 3
_ROLE_LABELS = {
    "profile": "画像",
    "match": "匹配",
    "report": "报告",
    "planner": "规划",
    "qa": "问答",
}

@dataclass
class PlanStepResult:
    subagent: str
    ok: bool
    reply: str = ""
    error: str = ""
    blackboard_updates: Dict[str, Any] = field(default_factory=dict)
    skill_versions: List[Dict[str, str]] = field(default_factory=list)


def detect_plan_template(message: str, plan_mode: bool = False) -> Optional[str]:
    """Compatibility shim: semantic template selection belongs to structured Lead."""
    return None


def build_plan_packets(
    template_id: str | Sequence[Dict[str, Any]],
    user_utterance: str,
    *,
    handoff_brief: str = "",
    goal_summary: str = "",
    capability_id: Optional[str] = None,
) -> List[TaskPacket]:
    """
    Give every step the bounded, self-contained task brief. Later steps remain
    lean (no raw utterance/history); blackboard pointers alone do not preserve
    the current request's target and constraints.
    """
    if isinstance(template_id, str):
        roles = PLAN_TEMPLATES.get(template_id)
        if not roles:
            raise ValueError(f"unknown plan template: {template_id}")
        step_specs = [{"role": role, "goal": "", "skill_ids": []} for role in roles]
        plan_id = template_id
    else:
        step_specs = validate_plan_steps(template_id, capability_id=capability_id)
        plan_id = "agent_plan"
    brief = (handoff_brief or goal_summary or user_utterance or "").strip()
    packets: List[TaskPacket] = []
    for i, spec in enumerate(step_specs):
        sub = str(spec["role"])
        step_id = f"{plan_id}:{i}:{sub}"
        task = str(spec.get("goal") or "").strip()
        task = task or brief
        goal = f"执行计划步骤 {i + 1}/{len(step_specs)}（{_ROLE_LABELS[sub]}）。"
        if task:
            goal += f"任务：{task[:400]}"
        if i == 0:
            packets.append(
                build_standard_packet(
                    subagent=sub,
                    goal=goal,
                    user_utterance=user_utterance,
                    plan_step_id=step_id,
                    skill_ids=list(spec.get("skill_ids") or []),
                    capability_id=capability_id,
                )
            )
        else:
            packets.append(
                build_lean_packet(
                    subagent=sub,
                    goal=goal,
                    plan_step_id=step_id,
                    skill_ids=list(spec.get("skill_ids") or []),
                    capability_id=capability_id,
                )
            )
    return packets


def validate_plan_steps(
    raw_steps: Any,
    *,
    capability_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Validate model-proposed serial steps; tools/permissions stay server-owned."""
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_PLAN_STEPS:
        raise ValueError(f"plan must contain 1..{MAX_PLAN_STEPS} steps")
    normalized: List[Dict[str, Any]] = []
    roles = set()
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ValueError(f"step {index + 1} must be an object")
        role = str(raw.get("role") or raw.get("subagent") or "").strip().lower()
        if role not in SUBAGENTS:
            raise ValueError(f"step {index + 1} role is not allowlisted")
        goal = str(raw.get("goal") or raw.get("task") or "").strip()
        if not goal or len(goal) > 400:
            raise ValueError(f"step {index + 1} goal must be 1..400 characters")
        raw_skills = raw.get("skill_ids", [])
        if not isinstance(raw_skills, list) or len(raw_skills) > 3:
            raise ValueError(f"step {index + 1} skill_ids must be a short list")
        allowed = allowed_skill_ids(role, capability_id=capability_id)
        skill_ids: List[str] = []
        for item in raw_skills:
            skill_id = str(item or "").strip()
            if skill_id not in allowed:
                raise ValueError(f"step {index + 1} skill is not allowlisted for role")
            if skill_id not in skill_ids:
                skill_ids.append(skill_id)
        # A plan is intentionally sequential; model-supplied dependency graphs,
        # arbitrary tools, and runtime permission overrides are not accepted.
        if any(key in raw for key in ("depends_on", "tools", "permissions", "parallel")):
            raise ValueError(f"step {index + 1} contains unsupported execution controls")
        normalized.append({"role": role, "goal": goal, "skill_ids": skill_ids})
        roles.add(role)
    if len(roles) > MAX_DISTINCT_ROLES:
        raise ValueError(f"plan exceeds {MAX_DISTINCT_ROLES} distinct roles")
    return normalized


def commit_step_updates(
    blackboard: Blackboard,
    updates: Dict[str, Any],
    writer: str,
    allowed_write_keys: Sequence[str],
) -> Dict[str, Any]:
    """Step-level commit: only keys in allowed_write_keys; raises on ACL fail."""
    committed = {}
    for key, value in (updates or {}).items():
        if key not in allowed_write_keys:
            continue
        blackboard.write(key, value, writer)
        committed[key] = value
    return committed


def run_plan(
    packets: List[TaskPacket],
    blackboard: Blackboard,
    invoke_fn,
    *,
    verify_updates=None,
) -> Tuple[List[PlanStepResult], str]:
    """
    Execute plan with step-level commit. invoke_fn(packet, blackboard) -> result dict.
    On failure, stop; prior successful commits remain (no full rollback).
    """
    results: List[PlanStepResult] = []
    summaries: List[str] = []

    for packet in packets:
        gate_err = blackboard.check_gate(packet.subagent)
        if gate_err:
            results.append(PlanStepResult(
                subagent=packet.subagent, ok=False, error=gate_err,
            ))
            summaries.append(f"[{packet.subagent}] 跳过: {gate_err}")
            break

        try:
            out = invoke_fn(packet, blackboard)
            # Model JSON is a proposal, not proof. Only a server-bound verifier
            # can commit references from this invocation's actual tool artifacts.
            updates = verify_updates(packet, out) if verify_updates and not out.get("incomplete") else {}
            committed = commit_step_updates(
                blackboard, updates, packet.subagent, packet.write_keys,
            )

            reply = out.get("reply") or ""
            ok, err = _evaluate_step_success(packet, blackboard, committed, out)
            results.append(PlanStepResult(
                subagent=packet.subagent,
                ok=ok,
                reply=reply,
                error=err,
                blackboard_updates=committed,
                skill_versions=list(out.get("skill_versions") or []),
            ))
            if ok:
                summaries.append(f"[{packet.subagent}] 完成")
            else:
                summaries.append(f"[{packet.subagent}] 未达成: {err or '无有效业务结果'}")
                break
        except Exception as e:
            results.append(PlanStepResult(
                subagent=packet.subagent, ok=False, error=str(e),
            ))
            summaries.append(f"[{packet.subagent}] 失败: {e}")
            break

    narrative = "\n".join(summaries)
    # Prefer last successful substantive reply; avoid appending failed hollow prose as "success"
    ok_replies = [r.reply for r in results if r.ok and r.reply]
    if ok_replies:
        narrative += "\n\n" + ok_replies[-1]
    elif results and results[-1].reply:
        narrative += "\n\n" + results[-1].reply
    return results, narrative


def _is_substantive_payload(val: Any, *, had_tools: bool) -> bool:
    """Only an authoritative completed business reference is a success."""
    return (isinstance(val, dict) and val.get("verified") is True
            and val.get("status") == "completed" and bool(val.get("resource_id"))
            and bool(val.get("source_task_id")))


def _evaluate_step_success(packet, blackboard, committed, out):
    if out.get("incomplete"):
        return False, "执行未完成"
    if not packet.write_keys:
        return True, ""
    if packet.subagent == "planner":
        plan = committed.get("plan.current")
        if (isinstance(plan, dict) and plan.get("persisted") is True
                and plan.get("status") == "completed" and plan.get("resource_id")):
            return True, ""
        return False, str(out.get("plan_persist_error") or "学习计划未持久化")
    # Never reuse an old blackboard artifact as this step's completion proof.
    if not _is_substantive_payload(committed.get(packet.write_keys[0]), had_tools=True):
        return False, "未写入本轮可验证的已完成业务结果"
    return True, ""
