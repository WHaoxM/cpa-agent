"""Durable Celery delivery for reviewed private entity links.

The append-only review journal is the authoritative request source. Pending
revisions can be rediscovered after a crash before broker delivery.
"""

from __future__ import annotations

import hashlib
import json
import logging

from celery.signals import worker_ready

from .agent_runtime.media.jobs import celery_app
from .personal_entity_links import publish_reviewed_entity_links
from .personal_graph import PersonalGraphSuperseded

GRAPH_QUEUE = "cpa.graph"
TERMINAL = frozenset({"completed", "superseded"})
logger = logging.getLogger(__name__)

PROJECTION_DDL = """CREATE TABLE IF NOT EXISTS personal_entity_link_projection (
    student_id VARCHAR(64) NOT NULL,
    source_task_id VARCHAR(64) NOT NULL,
    review_revision INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    payload_json JSON NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (student_id, source_task_id, review_revision),
    KEY idx_entity_link_retry (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def install_entity_link_projection(mysql):
    mysql.execute(PROJECTION_DDL, commit=True)


def projection_state(mysql, *, student_id, task_id, revision):
    rows = mysql.fetch_all(
        "SELECT payload_json FROM personal_entity_link_projection "
        "WHERE student_id=:sid AND source_task_id=:tid AND review_revision=:rev",
        {"sid": student_id, "tid": task_id, "rev": revision})
    value = rows[0]["payload_json"] if rows else None
    return json.loads(value) if isinstance(value, str) else value


def _record(mysql, *, student_id, task_id, revision, state, preserve_terminal=False):
    keep = "personal_entity_link_projection.status IN ('completed','superseded')" if preserve_terminal else "false"
    mysql.execute(
        "INSERT INTO personal_entity_link_projection "
        "(student_id,source_task_id,review_revision,status,payload_json) "
        "SELECT student_id,source_confirmation_task_id,revision,:status,CAST(:payload AS JSON) "
        "FROM resume_entity_review WHERE student_id=:sid AND source_confirmation_task_id=:tid "
        "AND revision=:rev "
        "ON DUPLICATE KEY UPDATE "
        f"payload_json=IF({keep},personal_entity_link_projection.payload_json,VALUES(payload_json)), "
        f"status=IF({keep},personal_entity_link_projection.status,VALUES(status)), "
        "updated_at=CURRENT_TIMESTAMP(6)",
        {"sid": student_id, "tid": task_id, "rev": revision,
         "status": state["status"], "payload": json.dumps(state, ensure_ascii=False)}, commit=True)


def request_entity_link_projection(mysql, *, student_id, task_id, revision):
    """Only after review journal commit; the journal survives broker failure."""
    state = projection_state(mysql, student_id=student_id, task_id=task_id, revision=revision)
    if state and state["status"] in TERMINAL:
        return state
    pending = {"status": "pending", "retryable": True, "source_task_id": task_id,
               "revision": revision}
    _record(mysql, student_id=student_id, task_id=task_id, revision=revision,
            state=pending, preserve_terminal=True)
    job_id = "cpa-entity-link-" + hashlib.sha256(
        (student_id + "\0" + task_id + "\0" + str(revision)).encode()).hexdigest()
    try:
        projection_task.apply_async(args=[student_id, task_id, revision], task_id=job_id,
                                    queue=GRAPH_QUEUE, retry=False)
    except Exception:
        pending = {**pending, "status": "retry_pending", "reason": "broker_unavailable"}
        _record(mysql, student_id=student_id, task_id=task_id, revision=revision,
                state=pending, preserve_terminal=True)
        return pending
    return {**pending, "status": "queued"}


def execute_entity_link_projection(*, student_id, task_id, revision, mysql=None, neo4j=None):
    from .db import get_mysql_client, get_neo4j_client

    mysql = mysql or get_mysql_client()
    prior = projection_state(mysql, student_id=student_id, task_id=task_id, revision=revision)
    if prior and prior["status"] in TERMINAL:
        return prior
    try:
        state = publish_reviewed_entity_links(mysql, neo4j or get_neo4j_client(),
            owner_student_id=student_id, task_id=task_id, revision=revision)
    except PersonalGraphSuperseded:
        state = {"status": "superseded", "retryable": False, "source_task_id": task_id,
                 "revision": revision}
    except Exception:
        state = {"status": "retry_pending", "retryable": True, "source_task_id": task_id,
                 "revision": revision, "reason": "projection_unavailable"}
    _record(mysql, student_id=student_id, task_id=task_id, revision=revision,
            state=state, preserve_terminal=True)
    return state


@celery_app.task(bind=True, name="cpa.graph.project_entity_links", acks_late=True,
                 reject_on_worker_lost=True, ignore_result=True, max_retries=3)
def projection_task(self, student_id, task_id, revision):
    state = execute_entity_link_projection(student_id=student_id, task_id=task_id,
                                           revision=revision)
    if state["status"] == "retry_pending" and self.request.retries < self.max_retries:
        raise self.retry(countdown=30 * (self.request.retries + 1))
    return state


def replay_entity_link_projections(mysql, *, student_id=None, limit=50, after_id=0):
    """Bounded latest-revision scan, including crash before initial dispatch."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be 1..200")
    if type(after_id) is not int or after_id < 0:
        raise ValueError("after_id must be nonnegative")
    owner_filter = " AND r.student_id=:sid" if student_id else ""
    rows = mysql.fetch_all(
        "SELECT r.id, r.student_id, r.source_confirmation_task_id AS task_id, r.revision "
        "FROM resume_entity_review r "
        "JOIN (SELECT student_id, source_confirmation_task_id, MAX(revision) AS revision "
        "FROM resume_entity_review GROUP BY student_id, source_confirmation_task_id) latest "
        "ON latest.student_id=r.student_id AND latest.source_confirmation_task_id=r.source_confirmation_task_id "
        "AND latest.revision=r.revision "
        "LEFT JOIN personal_entity_link_projection p ON p.student_id=r.student_id "
        "AND p.source_task_id=r.source_confirmation_task_id AND p.review_revision=r.revision "
        "WHERE r.id>:after_id AND COALESCE(p.status,'pending') NOT IN ('completed','superseded')" + owner_filter +
        " ORDER BY r.id LIMIT :lim",
        {"sid": student_id, "lim": limit + 1, "after_id": after_id})
    results = [request_entity_link_projection(mysql, student_id=row["student_id"],
               task_id=row["task_id"], revision=row["revision"]) for row in rows[:limit]]
    return {"dispatched": len(results), "has_more": len(rows) > limit,
            "next_after_id": rows[limit - 1]["id"] if len(rows) > limit else None,
            "results": results}


@worker_ready.connect
def replay_pending_entity_links_on_graph_worker_ready(sender=None, **_kwargs):
    """Recover commit-before-dispatch and unknown-task loss after worker upgrade.

    Celery 5.3.6 sends worker_ready with Consumer as sender; only the dedicated
    graph worker performs this bounded scan. A DB/broker outage stays visible in
    the journal and can be replayed manually, rather than fabricating success.
    """
    if not str(getattr(sender, "hostname", "")).startswith("cpa-graph@"):
        return
    try:
        from .db import get_mysql_client
        mysql = get_mysql_client()
        cursor = 0
        for _ in range(20):
            page = replay_entity_link_projections(mysql, limit=50, after_id=cursor)
            if not page["has_more"]:
                return
            cursor = page["next_after_id"]
        logger.warning("entity-link projection replay startup scan reached 1000 revisions; run paged replay")
    except Exception:
        logger.exception("entity-link projection startup replay failed; journal remains retryable")
