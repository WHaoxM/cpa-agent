"""Real Celery worker bootstrap for durable-dispatch transport tests.

Only the unrelated optional entity-normalizer/model lookup is substituted.
The queue, MySQL claim, domain scorer, transactional write, and owner reads
remain the production implementations.
"""

import sys

from app.pipeline.entity_extractor.matcher import SkillMatcher

SkillMatcher.normalizer = property(lambda self: None)

from app.services.agent_runtime.media.jobs import celery_app  # noqa: E402


if __name__ == "__main__":
    celery_app.worker_main(["worker", *sys.argv[1:]])
