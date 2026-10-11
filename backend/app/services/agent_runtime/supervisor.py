"""
Supervisor facade — delegates to ModeRuntime (Ask / Agent / Experts).

Kept for API compatibility: get_supervisor_service().chat(...)
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from .mode_runtime import ModeRuntime, get_mode_runtime


class SupervisorService:
    """Thin facade over ModeRuntime."""

    def __init__(self, runtime: Optional[ModeRuntime] = None):
        self._runtime = runtime or get_mode_runtime()

    def chat(
        self,
        message: str,
        *,
        session_id: Optional[str] = None,
        student_id: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        conversation_summary: str = "",
        session_context: Optional[Dict[str, Any]] = None,
        plan_mode: bool = False,
        confirmed_constraints: Optional[str] = None,
        interaction_mode: Optional[str] = None,
        attachment_ids: Optional[List[str]] = None,
        content_parts: Optional[List[Dict[str, Any]]] = None,
        voice_turn_id: Optional[str] = None,
        use_memory: Optional[bool] = None,
        prepared_turn: Optional[Dict[str, Any]] = None,
        session_lock: Optional[threading.Lock] = None,
    ) -> Dict[str, Any]:
        return self._runtime.chat(
            message,
            session_id=session_id,
            student_id=student_id,
            history=history,
            conversation_summary=conversation_summary,
            session_context=session_context,
            plan_mode=plan_mode,
            confirmed_constraints=confirmed_constraints,
            interaction_mode=interaction_mode,
            attachment_ids=attachment_ids,
            content_parts=content_parts,
            voice_turn_id=voice_turn_id,
            use_memory=use_memory,
            prepared_turn=prepared_turn,
            session_lock=session_lock,
        )


_supervisor: Optional[SupervisorService] = None
_supervisor_lock = threading.Lock()


def get_supervisor_service() -> SupervisorService:
    global _supervisor
    if _supervisor is None:
        with _supervisor_lock:
            if _supervisor is None:
                _supervisor = SupervisorService()
    return _supervisor
