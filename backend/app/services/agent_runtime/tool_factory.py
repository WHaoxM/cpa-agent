"""Build langchain tools filtered by subagent allowlist."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .tool_policy import tools_for

# Cache full adapted tool map by name
_ALL_TOOLS: Optional[Dict[str, Any]] = None


def _ensure_all_tools() -> Dict[str, Any]:
    global _ALL_TOOLS
    if _ALL_TOOLS is not None:
        return _ALL_TOOLS

    # Reuse langgraph adapter to avoid duplicating BaseTool→lc_tool logic
    from ..langgraph_agent import _build_langchain_tools

    tools = _build_langchain_tools()
    by_name: Dict[str, Any] = {}
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        if name:
            by_name[str(name)] = t
    _ALL_TOOLS = by_name
    return _ALL_TOOLS


def build_tools_for_subagent(
    subagent: str,
    *,
    student_id: Optional[str] = None,
    session_id: Optional[str] = None,
    extra_tools: Optional[Set[str]] = None,
) -> List[Any]:
    from .topology import assert_subagent_allowed

    assert_subagent_allowed(subagent)
    allow: Set[str] = tools_for(subagent) | set(extra_tools or ())
    all_tools = _ensure_all_tools()
    out = [all_tools[n] for n in sorted(allow) if n in all_tools and n != "attachment_read"]
    if "attachment_read" in allow:
        from ..tools.attachment_read import create_attachment_read_tool
        out.append(create_attachment_read_tool(student_id=student_id, session_id=session_id))
    return out
