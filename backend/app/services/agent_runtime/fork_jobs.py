"""
Fork-session style background jobs (Qoder Awareness alignment).

Main chat must not wait on: summary rollup, reflection, non-confirm MEMORY writes.
Jobs run after the response is prepared (daemon thread).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ...utils.logger import get_logger

logger = get_logger("cpa-agent.agent_runtime.fork")


@dataclass
class ForkJob:
    kind: str
    session_id: str
    payload: Dict[str, Any] = field(default_factory=dict)


_lock = threading.Lock()
_queue: List[ForkJob] = []
_handlers: Dict[str, Callable[[ForkJob], None]] = {}
# For tests: count enqueues / sync executions on calling thread
_stats = {"enqueued": 0, "ran": 0, "sync_forbidden_calls": 0}
_run_inline_for_tests = False


def register_handler(kind: str, fn: Callable[[ForkJob], None]) -> None:
    _handlers[kind] = fn


def get_fork_stats() -> Dict[str, int]:
    return dict(_stats)


def reset_fork_stats() -> None:
    _stats["enqueued"] = 0
    _stats["ran"] = 0
    _stats["sync_forbidden_calls"] = 0


def set_inline_for_tests(enabled: bool) -> None:
    """When True, enqueue runs handler inline (unit tests)."""
    global _run_inline_for_tests
    _run_inline_for_tests = enabled


def _run_job(job: ForkJob) -> None:
    handler = _handlers.get(job.kind)
    if not handler:
        logger.warning("no fork handler for kind=%s", job.kind)
        return
    try:
        handler(job)
        _stats["ran"] += 1
    except Exception as e:
        logger.warning("fork job failed kind=%s session=%s: %s", job.kind, job.session_id, e)


def enqueue(session_id: str, kind: str, payload: Optional[Dict[str, Any]] = None) -> str:
    job = ForkJob(kind=kind, session_id=session_id or "", payload=payload or {})
    _stats["enqueued"] += 1
    if _run_inline_for_tests:
        _run_job(job)
        return job.kind
    t = threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"fork-{kind}")
    t.start()
    return kind


def mark_sync_forbidden(name: str) -> None:
    """Tests can monkeypatch rollup to call this if invoked on request path."""
    _stats["sync_forbidden_calls"] += 1
    logger.debug("sync_forbidden invoked: %s", name)


def _handle_rollup(job: ForkJob) -> None:
    from ..session_manager import get_session_manager

    keep = int(job.payload.get("keep_messages") or 16)
    get_session_manager().maybe_rollup_summary(job.session_id, keep_messages=keep)


def _handle_reflect(job: ForkJob) -> None:
    from .memory_store import get_memory_store

    student_id = job.payload.get("student_id")
    summary = job.payload.get("summary") or ""
    if not student_id:
        return
    get_memory_store().reflect_session(
        str(student_id),
        job.session_id,
        str(summary),
        promote=bool(job.payload.get("promote", True)),
    )


register_handler("rollup_summary", _handle_rollup)
register_handler("reflect_session", _handle_reflect)
