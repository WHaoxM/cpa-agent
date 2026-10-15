"""Isolated Celery worker/Beat probe; never imported by production services."""

import os
from pathlib import Path

from app.services.agent_runtime.media.jobs import celery_app

if database := os.environ.get("CPA_CELERY_PROBE_DATABASE"):
    # Isolate the worker's real business task from the configured live DB.
    # The test creates and migrates this uniquely named database first.
    from app.config import Config

    Config.MYSQL_DATABASE = database


PROBE_QUEUE = os.environ["CPA_CELERY_PROBE_QUEUE"]
PROBE_MARKER = Path(os.environ["CPA_CELERY_PROBE_MARKER"])


@celery_app.task(name="cpa.test.transport_probe", ignore_result=True)
def transport_probe():
    PROBE_MARKER.write_text("worker-executed", encoding="utf-8")


# Override the production schedule in this test-only process. No business
# recovery or source-refresh task is published by the probe Beat.
celery_app.conf.beat_schedule = {
    "transport-probe": {
        "task": "cpa.test.transport_probe",
        "schedule": 2.0,
        "options": {"queue": PROBE_QUEUE},
    }
}
