"""Celery publisher/replay/worker task for durable Agent runs.

The DB outbox is committed before ``apply_async``. The Celery payload contains
only ``run_id``; the worker rechecks run eligibility and owner metadata from
MySQL. The executor reads the durable input snapshot, persists phases and
results to MySQL, and uses the existing MySQL-backed LangGraph saver only as
the restartable phase driver. The Celery result backend is not authoritative.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from .agent_run_dispatch import AgentRunDispatchStore, get_agent_run_dispatch_store
from .agent_run_store import (
    AgentRunConflict,
    AgentRunRepository,
    get_agent_run_repository,
)
from .media.jobs import AGENT_QUEUE, celery_app
from ...utils.logger import get_logger


logger = get_logger("cpa-agent.agent_runtime.agent_run_jobs")


class _ClaimLeaseKeeper:
    """Keep a long-running read-only model phase within its MySQL claim lease."""

    def __init__(self, dispatch_store: AgentRunDispatchStore, run_id: str,
                 claim_token: str, *, interval_seconds: int = 60):
        self.dispatch_store = dispatch_store
        self.run_id = run_id
        self.claim_token = claim_token
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"agent-run-lease-{run_id[-8:]}",
            daemon=True,
        )

    def start(self):
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def is_lost(self) -> bool:
        return self._lost.is_set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                if not self.dispatch_store.renew_claim(
                    self.run_id, claim_token=self.claim_token,
                ):
                    self._lost.set()
                    return
            except Exception:
                logger.exception("Agent run claim renewal failed run_id=%s", self.run_id)
                self._lost.set()
                return


def dispatch_agent_run(
    run_id: str,
    *,
    repository: Optional[AgentRunRepository] = None,
    dispatch_store: Optional[AgentRunDispatchStore] = None,
    allow_not_dispatched: bool = False,
) -> str:
    """Attempt one fenced publish; failure leaves durable MySQL retry intent."""
    repository = repository or get_agent_run_repository()
    dispatch_store = dispatch_store or AgentRunDispatchStore(repository)
    reservation = dispatch_store.reserve_publish(
        run_id,
        allow_not_dispatched=allow_not_dispatched,
    )
    if not reservation.get("reserved"):
        return str(reservation["state"])

    token = reservation["publish_token"]
    try:
        agent_run_task.apply_async(
            args=[run_id],
            task_id="cpa-agent-" + run_id.removeprefix("run_"),
            queue=AGENT_QUEUE,
            retry=False,
        )
    except Exception:
        logger.exception("Agent run broker publish failed run_id=%s", run_id)
        try:
            return dispatch_store.finish_publish(
                run_id,
                publish_token=token,
                published=False,
            )
        except Exception:
            logger.exception("Agent run publish failure could not be persisted run_id=%s", run_id)
            return "publishing_unverified"

    try:
        return dispatch_store.finish_publish(
            run_id,
            publish_token=token,
            published=True,
        )
    except Exception:
        # A broker ack with an uncertain SQL settle can be replayed after the
        # publisher lease expires; the worker's MySQL claim fences duplicates.
        logger.exception("Agent run broker ack could not be persisted run_id=%s", run_id)
        return "publishing_unverified"


def replay_agent_run_intents(
    *,
    limit: int = 50,
    dispatch_store: Optional[AgentRunDispatchStore] = None,
) -> Dict[str, Any]:
    """Publish one bounded MySQL page; Redis/Celery never define eligibility."""
    dispatch_store = dispatch_store or get_agent_run_dispatch_store()
    run_ids = dispatch_store.due_run_ids(limit=limit)
    outcomes = []
    for run_id in run_ids:
        try:
            state = dispatch_agent_run(
                run_id,
                repository=dispatch_store.repository,
                dispatch_store=dispatch_store,
            )
        except Exception:
            logger.exception("Agent run replay failed run_id=%s", run_id)
            state = "replay_error"
        outcomes.append({"run_id": run_id, "dispatch_status": state})
    return {
        "attempted": len(outcomes),
        "published": sum(item["dispatch_status"] == "published" for item in outcomes),
        "has_more": len(run_ids) == limit,
        "results": outcomes,
    }


@celery_app.task(
    bind=True,
    name="cpa.agent.run",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def agent_run_task(self, run_id: str) -> Dict[str, Any]:
    """Claim one delivery and execute only MySQL-persisted safe Agent phases."""
    dispatch_store = get_agent_run_dispatch_store()
    try:
        claim = dispatch_store.claim_worker(run_id)
    except Exception:
        logger.exception("Agent run transport claim failed run_id=%s", run_id)
        raise
    if claim is None:
        return {"run_id": run_id, "dispatch_status": "duplicate_or_not_eligible"}
    keeper = _ClaimLeaseKeeper(
        dispatch_store, run_id, claim["claim_token"],
    ).start()
    try:
        from .agent_run_executor import AgentRunExecutor

        executor = AgentRunExecutor(dispatch_store.repository, dispatch_store)
        run = executor.execute_claim(
            run_id,
            claim_token=claim["claim_token"],
            student_id=claim["student_id"],
        )
        dispatch_status = dispatch_store.get_for_owner(
            run_id, student_id=claim["student_id"],
        )["status"]
        return {
            "run_id": run_id,
            "dispatch_status": dispatch_status,
            "run_status": run["status"],
            "execution_started": True,
        }
    except AgentRunConflict:
        # A duplicate/stale delivery is acknowledged only after MySQL confirms
        # that this worker no longer owns the claim. A current claim conflict
        # is a real executor failure and must be retried/inspected, not hidden.
        try:
            current = dispatch_store.get_for_owner(
                run_id, student_id=claim["student_id"],
            )
            if not dispatch_store.claim_is_current(run_id, claim["claim_token"]):
                return {
                    "run_id": run_id,
                    "dispatch_status": "claim_fenced",
                    "current_dispatch_status": current["status"],
                    "execution_started": False,
                }
        except Exception:
            logger.exception("Agent run claim conflict could not be verified run_id=%s", run_id)
            raise
        logger.exception("Agent run executor conflict while claim is current run_id=%s", run_id)
        raise
    except Exception:
        # Late acknowledgement lets Celery redeliver. Expired claims with no
        # active step can resume from authoritative MySQL; an active/uncertain
        # step is moved to needs_reconciliation by the dispatch state machine.
        logger.exception("Agent run execution failed run_id=%s", run_id)
        raise
    finally:
        keeper.stop()


@celery_app.task(
    name="cpa.agent.replay",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def automatic_agent_run_replay_task() -> Dict[str, Any]:
    return replay_agent_run_intents(limit=50)
