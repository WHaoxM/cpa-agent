"""
PermissionMode + deny/ask/allow evaluation (Claude/Qoder-aligned).

Ask → dont_ask (unlisted = deny; write tools invisible)
Agent → default (reads allow; scoped writes allow within subagent list)
Experts → plan_allow (only tools in plan step allowlist)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Set

from .modes import InteractionMode
from .tool_policy import ASK_READONLY_TOOLS, tools_for

# Tools that must never appear in Ask
ASK_DENY_TOOLS = frozenset({
    "pipeline_run",
    "portrait_generate",
    "report_generate",
    "skill_match",
    "skill_extract",
    "jd_cleaner",
    "learning_path",
    "skill_recommend",
    "db_query",
    "semantic_search",
    "llm_reason",
    "normalize_entity",
    # career_data_read / graph_query / attachment_read are eligible only through
    # a server-selected explicit read allowlist; all writes stay denied.
})

# Tools that need a bound student scope when session has student_id
STUDENT_SCOPED_TOOLS = frozenset({
    "career_data_read",
    "db_query",
    "pipeline_run",
    "portrait_generate",
    "report_generate",
    "skill_extract",
    "skill_match",
    "learning_path",
    "skill_recommend",
    "attachment_read",
})


class PermissionMode(str, Enum):
    DONT_ASK = "dont_ask"       # Ask interaction
    DEFAULT = "default"         # Agent
    PLAN_ALLOW = "plan_allow"   # Experts


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PermissionVerdict:
    decision: Decision
    reason: str = ""
    pending_tool: Optional[Dict[str, Any]] = None  # reserved for future UI confirm


def permission_mode_for(interaction: InteractionMode) -> PermissionMode:
    if interaction == InteractionMode.ASK:
        return PermissionMode.DONT_ASK
    if interaction == InteractionMode.EXPERTS:
        return PermissionMode.PLAN_ALLOW
    return PermissionMode.DEFAULT


def visible_tools_for(
    interaction: InteractionMode,
    subagent: str,
    *,
    plan_tool_allowlist: Optional[Set[str]] = None,
) -> Set[str]:
    """Model-visible tool names for this turn."""
    if interaction == InteractionMode.ASK:
        # No ambient Ask tools: the server must select a bounded read-only set
        # for this request. Attachment reads remain available only when the
        # caller explicitly supplies that per-turn capability.
        if subagent != "qa":
            return set()
        if plan_tool_allowlist:
            return set(ASK_READONLY_TOOLS) & set(plan_tool_allowlist)
        return set()
    allowed = tools_for(subagent)
    if interaction == InteractionMode.EXPERTS and plan_tool_allowlist is not None:
        return allowed & set(plan_tool_allowlist)
    if plan_tool_allowlist is not None and interaction == InteractionMode.AGENT:
        # optional narrow allow
        return allowed & set(plan_tool_allowlist) if plan_tool_allowlist else allowed
    return set(allowed)


def evaluate(
    tool_name: str,
    args: Dict[str, Any],
    *,
    interaction: InteractionMode,
    subagent: str,
    plan_tool_allowlist: Optional[Set[str]] = None,
) -> PermissionVerdict:
    """
    Order: deny → ask → allow (Claude-style).
    HTTP has no interactive ask: ASK decisions become DENY with pending_tool reserved.
    """
    name = tool_name or ""
    pmode = permission_mode_for(interaction)
    visible = visible_tools_for(
        interaction, subagent, plan_tool_allowlist=plan_tool_allowlist,
    )

    # Ask: deny everything not in the server-selected, read-only visible set.
    if pmode == PermissionMode.DONT_ASK:
        if name not in visible:
            return PermissionVerdict(
                Decision.DENY,
                reason="Ask 模式不可调用业务工具；请切换到 Agent 或 Experts。",
                pending_tool={"tool_name": name, "parameters": dict(args or {})},
            )
        # attachment_read allowed — fall through

    if name not in visible:
        return PermissionVerdict(
            Decision.DENY,
            reason=f"工具 '{name}' 对当前子 Agent/模式不可见。",
        )

    if name in ASK_DENY_TOOLS and interaction == InteractionMode.ASK:
        return PermissionVerdict(Decision.DENY, reason="Ask 模式拒绝该工具。")

    # Experts: only plan-allowlisted tools
    if pmode == PermissionMode.PLAN_ALLOW and plan_tool_allowlist is not None:
        if name not in plan_tool_allowlist:
            return PermissionVerdict(
                Decision.DENY,
                reason=f"Experts 计划外工具 '{name}' 已拒绝。",
            )

    # db_query without any id binding → deny (no silent list)
    if name == "db_query":
        has_scope = any(
            args.get(k) not in (None, "")
            for k in ("student_id", "job_id", "portrait_id", "report_id", "session_id", "job_portrait_id")
        )
        # query text alone is not enough for student tables
        if not has_scope and "student" in str(args.get("query", "")).lower():
            return PermissionVerdict(
                Decision.DENY,
                reason="db_query 缺少 student_id 等绑定参数，拒绝执行以防越权列表。",
            )

    return PermissionVerdict(Decision.ALLOW, reason="ok")
