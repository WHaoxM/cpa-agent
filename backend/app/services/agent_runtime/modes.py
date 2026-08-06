"""Interaction modes: Ask / Agent / Experts (Qoder-aligned first-class modes)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class InteractionMode(str, Enum):
    ASK = "ask"
    AGENT = "agent"
    EXPERTS = "experts"


DEFAULT_MODE = InteractionMode.ASK


@dataclass(frozen=True)
class ModeBudget:
    """Per-mode capability budget."""
    allow_tools: bool
    allow_plan: bool
    allow_blackboard_business_write: bool
    allow_memory_read: bool
    allow_memory_write_sync: bool  # only confirmed USER constraints
    allow_llm_router: bool
    ask_greeting_accel: bool  # Ask-only narrow accel


BUDGETS: Dict[InteractionMode, ModeBudget] = {
    # Ask = conversation STM only (Claude/Qoder). Student LTM is NOT auto-mounted.
    # Opt-in via request use_memory=true when product explicitly wants it.
    InteractionMode.ASK: ModeBudget(
        allow_tools=False,
        allow_plan=False,
        allow_blackboard_business_write=False,
        allow_memory_read=False,
        allow_memory_write_sync=True,  # confirmed USER constraints still writable
        allow_llm_router=False,
        ask_greeting_accel=True,
    ),
    InteractionMode.AGENT: ModeBudget(
        allow_tools=True,
        allow_plan=False,
        allow_blackboard_business_write=True,
        allow_memory_read=True,
        allow_memory_write_sync=True,
        allow_llm_router=True,  # only when keyword miss + ambiguous
        ask_greeting_accel=False,
    ),
    InteractionMode.EXPERTS: ModeBudget(
        allow_tools=True,
        allow_plan=True,
        allow_blackboard_business_write=True,
        allow_memory_read=True,
        allow_memory_write_sync=True,
        allow_llm_router=False,
        ask_greeting_accel=False,
    ),
}


_TASK_UPGRADE_HINTS = (
    "报告", "匹配", "画像", "学习路径", "pipeline", "生成职业", "一键出报告",
    "完整职业方案", "端到端", "从简历到",
)


def parse_interaction_mode(
    value: Optional[str],
    *,
    plan_mode: bool = False,
    session_mode: Optional[str] = None,
) -> InteractionMode:
    """
    Resolve mode: explicit request > plan_mode forces experts >
    session persisted > default ask.
    """
    if plan_mode:
        return InteractionMode.EXPERTS
    raw = (value or session_mode or "").strip().lower()
    if raw in ("ask", "chat", "qa_mode"):
        return InteractionMode.ASK
    if raw in ("agent",):
        return InteractionMode.AGENT
    if raw in ("experts", "expert", "multi"):
        return InteractionMode.EXPERTS
    return DEFAULT_MODE


def budget_for(mode: InteractionMode) -> ModeBudget:
    return BUDGETS[mode]


def detect_upgrade_hint(message: str, mode: InteractionMode) -> Optional[Dict[str, str]]:
    """Ask mode: suggest upgrade when user asks for tool-heavy work; never auto-escalate."""
    if mode != InteractionMode.ASK:
        return None
    text = message or ""
    if any(k in text for k in ("完整职业方案", "一键出报告", "端到端", "从简历到报告")):
        return {
            "to": InteractionMode.EXPERTS.value,
            "reason": "该请求适合 Experts 多步骤协作（画像→匹配→报告）。",
        }
    if any(k in text for k in _TASK_UPGRADE_HINTS):
        return {
            "to": InteractionMode.AGENT.value,
            "reason": "该请求需要调用业务工具执行，建议切换到 Agent 模式。",
        }
    return None


def persist_mode_into_context(context: Dict[str, Any], mode: InteractionMode) -> Dict[str, Any]:
    ctx = dict(context or {})
    orch = dict(ctx.get("orchestration") or {}) if isinstance(ctx.get("orchestration"), dict) else {}
    # also nest under blackboard orchestration.meta via session layer
    ctx["interaction_mode"] = mode.value
    orch["interaction_mode"] = mode.value
    ctx["orchestration"] = orch
    return ctx
