"""Load the production Celery app against the disposable workbench schema."""

import os

from app.services.agent_runtime.media.jobs import celery_app
from app.config import Config


database = os.environ.get("CPA_WORKBENCH_CELERY_DATABASE")
if not database:
    raise RuntimeError("CPA_WORKBENCH_CELERY_DATABASE is required for this probe")

# Some developer environments load repo-root .env after subprocess startup.
# Pin the test worker and Beat to the test-owned schema after Config is loaded.
Config.MYSQL_DATABASE = database

