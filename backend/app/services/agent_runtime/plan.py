"""Plan whitelist + step-level blackboard commit."""

from __future__ import annotations

import re
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

_END_TO_END_PATTERNS = [
    r"完整职业方案",
    r"从简历到报告",
    r"一键出报告",
    r"端到端",
    r"全流程",
]


@dataclass
class PlanStepResult:
    subagent: str
    ok: bool
    reply: str = ""
    error: str = ""
    blackboard_updates: Dict[str, Any] = field(default_factory=dict)


def detect_plan_template(message: str, plan_mode: bool = False) -> Optional[str]:
    """
    Whitelist template matcher only (hint for Lead).
    Intent gating belongs to experts_lead_decide — not keyword meta lists here.
    """
    text = (message or "").strip()
    if not text:
        return None

    for pat in _END_TO_END_PATTERNS:
        if re.search(pat, text):
            return "end_to_end"
    if re.search(r"画像.*匹配|先画像再匹配", text):
        return "profile_match"
    if re.search(r"匹配.*报告|基于匹配.*报告", text):
        return "match_report"
    if re.search(r"匹配", text) and re.search(r"规划|学习路径", text) and "报告" not in text:
        return "match_planner"
    if plan_mode and re.search(r"(从简历|生成报告|一键|全流程|端到端).{0,12}(报告|画像|匹配)", text):
        return "end_to_end"
    return None


def build_plan_packets(
    template_id: str,
    user_utterance: str,
    *,
    handoff_brief: str = "",
    goal_summary: str = "",
) -> List[TaskPacket]:
    """
    Build step packets. First step gets a Kimi-style self-contained brief
    (handoff_brief / goal_summary) instead of raw chat noise alone.
    """
    steps = PLAN_TEMPLATES.get(template_id)
    if not steps:
        raise ValueError(f"unknown plan template: {template_id}")
    brief = (handoff_brief or goal_summary or user_utterance or "").strip()
    packets: List[TaskPacket] = []
    for i, sub in enumerate(steps):
        step_id = f"{template_id}:{i}:{sub}"
        if i == 0 and brief:
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
            updates = out.get("blackboard_updates") or {}
            committed = commit_step_updates(
                blackboard, updates, packet.subagent, packet.write_keys,
            )
            # Only heuristic-commit when tools produced evidence (not hollow LLM prose)
            _maybe_heuristic_commit(blackboard, packet, out, committed)

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
    """Structural check: ids / rich summary — not hollow present:true stubs."""
    if val is None:
        return False
    if not isinstance(val, dict):
        return len(str(val).strip()) >= 16
    if val.get("source") == "heuristic_commit" and not had_tools:
        return False
    # Projection stubs / empty markers
    keys = set(val.keys())
    if keys <= {"present", "linked"} or keys == {"present"}:
        return False
    if any(val.get(k) for k in ("id", "portrait_id", "match_id", "report_id", "task_id")):
        return True
    summary = str(val.get("summary") or "").strip()
    if len(summary) < 24:
        return False
    # Hollow “I can't” prose written as summary
    failish = ("无法", "缺少", "不足", "incomplete", "失败", "不能生成", "暂无", "尚未")
    if sum(1 for m in failish if m in summary) >= 1 and not had_tools:
        return False
    return True


def _evaluate_step_success(
    packet: TaskPacket,
    blackboard: Blackboard,
    committed: Dict[str, Any],
    out: Dict[str, Any],
) -> Tuple[bool, str]:
    """Business success ≠ invoke without exception (Claude/Qoder: evidence over status flags)."""
    write_keys = list(packet.write_keys or [])
    if not write_keys:
        return True, ""
    primary = write_keys[0]
    val = committed.get(primary)
    if val is None and blackboard.has(primary):
        val = blackboard.read_slice([primary]).get(primary)
    had_tools = bool(out.get("tool_calls") or [])
    if not _is_substantive_payload(val, had_tools=had_tools):
        return False, "未写入有效业务结果"
    return True, ""


_HEURISTIC_FAIL_MARKERS = (
    "无法", "缺少", "不足", "incomplete", "失败", "不能生成", "暂无", "尚未",
)


def _maybe_heuristic_commit(
    blackboard: Blackboard,
    packet: TaskPacket,
    out: Dict[str, Any],
    already: Dict[str, Any],
) -> None:
    """If tools ran but no blackboard JSON, write a light summary pointer."""
    if already:
        return
    write_keys = packet.write_keys
    if not write_keys:
        return
    primary = write_keys[0]
    if blackboard.has(primary):
        return
    tools = out.get("tool_calls") or []
    if not tools:
        # No tool evidence → do not fake a successful business write
        return
    preview = (out.get("reply") or "")[:200]
    if any(m in preview for m in _HEURISTIC_FAIL_MARKERS):
        return
    tool_names = [t.get("tool_name") for t in tools if isinstance(t, dict)]
    try:
        blackboard.write(
            primary,
            {
                "summary": preview or f"{packet.subagent} step done",
                "tools": tool_names,
                "source": "heuristic_commit",
            },
            packet.subagent,
        )
        already[primary] = blackboard.read_slice([primary]).get(primary)
    except BlackboardError:
        pass
