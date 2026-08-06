"""Thin wrapper around monolith LangGraphAgentService for mode=langgraph_legacy."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def chat_legacy(
    message: str,
    session_id: Optional[str] = None,
    student_context: Optional[Dict] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    from ..langgraph_agent import get_langgraph_agent

    agent = get_langgraph_agent()
    result = agent.chat(
        message=message,
        session_id=session_id,
        student_context=student_context,
        history=history,
    )
    result["engine"] = "langgraph_legacy"
    return result


def chat_stream_legacy(
    message: str,
    session_id: Optional[str] = None,
    student_context: Optional[Dict] = None,
    history: Optional[List[Dict[str, str]]] = None,
):
    from ..langgraph_agent import get_langgraph_agent

    agent = get_langgraph_agent()
    yield from agent.chat_stream(
        message=message,
        session_id=session_id,
        student_context=student_context,
        history=history,
    )
