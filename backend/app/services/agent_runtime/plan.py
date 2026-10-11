"""Plan whitelist + step-level blackboard commit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .blackboard import Blackboard, BlackboardError
from .task_packet import TaskPacket, build_lean_packet, build_standard_packet

# Whitelisted plan templates (plan D)
PLAN_TEMPLATES: Dict[str, List[str]] = {
    "end_to_end": ["profile", "match", "report"],
    "profile_match": ["profile", "match"],
    "match_report": ["match", "report"],
    "match_planner": ["match", "planner"],
}

@dataclass
class PlanStepResult:
    subagent: str
    ok: bool
    reply: str = ""
    error: str = ""
    blackboard_updates: Dict[str, Any] = field(default_factory=dict)


def detect_plan_template(message: str, plan_mode: bool = False) -> Optional[str]:
    """Compatibility shim: semantic template selection belongs to structured Lead."""
    return None


def build_plan_packets(
    template_id: str,
    user_utterance: str,
    *,
    handoff_brief: str = "",
    goal_summary: str = "",
) -> List[TaskPacket]:
    """
    Give every step the bounded, self-contained task brief. Later steps remain
    lean (no raw utterance/history); blackboard pointers alone do not preserve
    the current request's target and constraints.
    """
    steps = PLAN_TEMPLATES.get(template_id)
    if not steps:
        raise ValueError(f"unknown plan template: {template_id}")
    brief = (handoff_brief or goal_summary or user_utterance or "").strip()
    packets: List[TaskPacket] = []
    for i, sub in enumerate(steps):
        step_id = f"{template_id}:{i}:{sub}"
        if brief:
            goal = f"执行计划步骤 {i + 1}/{len(steps)}: {sub}。任务：{brief[:400]}"
        else:
            goal = f"执行计划步骤 {i + 1}/{len(steps)}: {sub}"
        if i == 0:
            packets.append(
                build_standard_packet(
                    subagent=sub,
                    goal=goal,
                    user_utterance=user_utterance,
                    plan_step_id=step_id,
                )
            )
        else:
            packets.append(
                build_lean_packet(
                    subagent=sub,
                    goal=goal,
                    plan_step_id=step_id,
                )
            )
    return packets


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

    narrative = "计划执行结果:\n" + "\n".join(summaries)
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
