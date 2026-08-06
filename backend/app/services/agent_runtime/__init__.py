"""
Agent Runtime — Ask / Agent / Experts + Blackboard + Memory Store.

Default entry: get_supervisor_service() / get_mode_runtime()
Legacy monolith: mode=langgraph_legacy → legacy.chat()

Package init stays light: media/file upload must not import LangGraph.
Heavy modules load on first use via __getattr__ / getters.

Important: never use `from . import submodule` inside __getattr__ or getters
for names that are also lazy-exported — that re-enters __getattr__ and
recurses until RecursionError. Use importlib.import_module instead.
"""

from __future__ import annotations

import importlib
from typing import Any

from .modes import InteractionMode, DEFAULT_MODE, parse_interaction_mode

__all__ = [
    "SupervisorService",
    "get_supervisor_service",
    "ModeRuntime",
    "get_mode_runtime",
    "InteractionMode",
    "DEFAULT_MODE",
    "parse_interaction_mode",
    "fork_jobs",
    "tool_audit",
]


def get_mode_runtime():
    from .mode_runtime import get_mode_runtime as _get

    return _get()


def get_supervisor_service():
    # Side-effect: register fork audit handler once chat path is used.
    # Must use importlib — `from . import tool_audit` re-enters __getattr__.
    importlib.import_module(__name__ + ".tool_audit")
    from .supervisor import get_supervisor_service as _get

    return _get()


def __getattr__(name: str) -> Any:
    if name == "ModeRuntime":
        from .mode_runtime import ModeRuntime

        return ModeRuntime
    if name == "SupervisorService":
        from .supervisor import SupervisorService

        return SupervisorService
    if name in ("fork_jobs", "tool_audit"):
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
