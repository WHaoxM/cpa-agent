"""Durable Celery transport for long-running media parsing jobs.

The business record in :mod:`file_store` remains authoritative.  Celery only
provides durable delivery/redelivery through the existing Redis service.  This
keeps OCR state queryable even when Celery's optional result backend is absent.

Celery's late acknowledgement can redeliver a job after a worker loss, so the
worker entries deliberately call idempotent ``AgentFileService`` methods and
use a deterministic task id supplied by the persisted business record.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from celery import Celery

from ....utils.logger import get_logger

logger = get_logger("cpa-agent.agent_runtime.media.jobs")

MEDIA_QUEUE = "cpa.media"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def _redis_url() -> str:
    explicit = (
        os.environ.get("CPA_MEDIA_BROKER_URL")
        or os.environ.get("CELERY_BROKER_URL")
        or os.environ.get("REDIS_URL")
    )
    if explicit:
        return explicit
    try:
        from ....config import Config

        return str(Config.REDIS_URL)
    except Exception:
        return "redis://localhost:6379/0"


celery_app = Celery("cpa_agent_media", broker=_redis_url())
celery_app.conf.update(
    task_default_queue=MEDIA_QUEUE,
    task_routes={"cpa.media.*": {"queue": MEDIA_QUEUE}, "cpa.graph.*": {"queue": "cpa.graph"}},
    imports=("app.services.personal_graph_jobs", "app.services.personal_entity_link_jobs"),
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_backend=None,
    task_ignore_result=True,
    task_track_started=True,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_timeout=2,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        # Keep this above the expected maximum OCR duration or Redis can
        # redeliver a still-running task. No hard Celery time limit is set:
        # killing a late-ack child with reject_on_worker_lost would create a
        # poison-message redelivery loop. The Sidecar has its own HTTP timeout.
        "visibility_timeout": 6 * 60 * 60,
        "socket_connect_timeout": 2,
        "socket_timeout": 5,
    },
    timezone="Asia/Shanghai",
    enable_utc=True,
)


def build_media_job_id(kind: str, file_id: str, attempt: int) -> str:
    """Return a stable, broker-safe id that is also persisted with the file."""
    clean_kind = _SAFE_ID.sub("-", str(kind or "media"))
    clean_file = _SAFE_ID.sub("-", str(file_id or "missing"))
    return f"cpa-media-{clean_kind}-{clean_file}-{max(1, int(attempt))}"


def execute_media_job(kind: str, file_id: str) -> Optional[Dict[str, Any]]:
    """Worker body kept separate from Celery so recovery is unit-testable."""
    from .file_store import get_agent_file_service

    service = get_agent_file_service()
    if kind == "ingest":
        return service.complete_ingest(str(file_id))
    if kind == "upgrade":
        return service.complete_upgrade(str(file_id))
    raise ValueError(f"unsupported media job kind: {kind}")


@celery_app.task(
    bind=True,
    name="cpa.media.execute",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def media_task(self, kind: str, file_id: str) -> Optional[Dict[str, Any]]:
    logger.info(
        "media worker start task_id=%s kind=%s file_id=%s redelivered=%s",
        self.request.id,
        kind,
        file_id,
        bool((self.request.delivery_info or {}).get("redelivered")),
    )
    result = execute_media_job(kind, file_id)
    logger.info(
        "media worker done task_id=%s kind=%s file_id=%s status=%s",
        self.request.id,
        kind,
        file_id,
        (result or {}).get("status"),
    )
    return result


def enqueue_media_job(kind: str, file_id: str, *, task_id: str) -> str:
    """Publish without silently falling back to an in-process thread."""
    media_task.apply_async(
        args=[str(kind), str(file_id)],
        task_id=task_id,
        queue=MEDIA_QUEUE,
        retry=False,
    )
    return task_id


__all__ = [
    "MEDIA_QUEUE",
    "build_media_job_id",
    "celery_app",
    "enqueue_media_job",
    "execute_media_job",
    "media_task",
]
