"""Periodic official-source verification, separate from public HTTP reads."""

from .agent_runtime.media.jobs import celery_app
from .job_source_verification import refresh_verified_feed
from ..utils.logger import get_logger

logger = get_logger("cpa-agent.job_source_verification_jobs")


@celery_app.task(name="cpa.catalog.refresh_source_feed", ignore_result=True)
def refresh_source_feed_task():
    try:
        result = refresh_verified_feed()
    except Exception as exc:
        # refresh_verified_feed invalidates its previous cache on failure.
        # Beat retries on the next interval rather than using stale content.
        logger.warning("官方来源定时核验失败: %s", exc)
        return {"status": "unavailable"}
    return {"status": "ready", "verified_at": result["verified_at"]}
