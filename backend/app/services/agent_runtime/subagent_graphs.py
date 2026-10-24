"""
Business subagents (profile/match/report/planner).

Execution goes through scoped_invoke with gated tools so each turn gets
PreToolUse + dual-channel results (compiled static graphs cannot carry session scope).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from .blackboard import Blackboard
from .modes import InteractionMode
from .task_packet import TaskPacket

BUSINESS_SUBAGENTS = ("profile", "match", "report", "planner")


def invoke_subagent_graph(
    packet: TaskPacket,
    blackboard: Blackboard,
    *,
    session_id: Optional[str] = None,
    interaction_mode: InteractionMode = InteractionMode.AGENT,
    student_id: Optional[str] = None,
    plan_tool_allowlist: Optional[Set[str]] = None,
    allow_artifact_commit: bool = True,
    tool_event_context=None,
    tool_event_sink=None,
) -> Dict[str, Any]:
    from .scoped_invoke import scoped_invoke

    out = scoped_invoke(
        packet,
        blackboard,
        allow_tools=True,
        interaction_mode=interaction_mode,
        session_id=session_id,
        student_id=student_id,
        plan_tool_allowlist=plan_tool_allowlist,
        allow_artifact_commit=allow_artifact_commit,
        tool_event_context=tool_event_context,
        tool_event_sink=tool_event_sink,
    )
    out["engine"] = f"graph:{packet.subagent}"
    return out
