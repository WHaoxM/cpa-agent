"""Post-commit graph projection using the existing journal and Celery broker.

The completed, confirmed L0 task is the durable request. Missing projection
state means pending, so a crash between its commit and broker dispatch cannot
lose the work. A domain projection table holds acknowledgement state without
mutating the L0 result JSON/hash used by page and Agent references.
"""

from __future__ import annotations

import hashlib
import json

from .agent_runtime.media.jobs import celery_app
from .personal_graph import PersonalGraphSuperseded, publish_confirmed_personal_graph

GRAPH_QUEUE = "cpa.graph"
TERMINAL = {"completed", "superseded"}

PROJECTION_DDL = """CREATE TABLE IF NOT EXISTS personal_graph_projection (
    student_id VARCHAR(64) NOT NULL,
    source_task_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    payload_json JSON NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (student_id, source_task_id),
    KEY idx_graph_projection_status (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""


def install_personal_graph_projection(mysql):
    """Explicit additive migration; never run DDL in confirmation requests."""
    mysql.execute(PROJECTION_DDL, commit=True)


def _task(mysql, student_id, task_id):
    rows = mysql.fetch_all(
        "SELECT result_json FROM pipeline_task WHERE task_id=:tid AND student_id=:sid "
        "AND pipeline='L0_parse_resume' AND status='completed'", {"tid": task_id, "sid": student_id})
    value = rows[0].get("result_json") if rows else None
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict) or value.get("confirmed") is not True:
        raise LookupError("已确认来源任务不存在")
    states = mysql.fetch_all("SELECT payload_json FROM personal_graph_projection WHERE student_id=:sid AND source_task_id=:tid",
                             {"sid": student_id, "tid": task_id})
    if states:
        state = states[0]["payload_json"]
        value = {**value, "personal_graph_projection": json.loads(state) if isinstance(state, str) else state}
    return value


def _record(mysql, student_id, task_id, state, *, preserve_terminal=False):
    keep = "personal_graph_projection.status IN ('completed','superseded')" if preserve_terminal else "false"
    mysql.execute(
        "INSERT INTO personal_graph_projection(student_id,source_task_id,status,payload_json) "
        "SELECT student_id,task_id,:status,CAST(:state AS JSON) FROM pipeline_task "
        "WHERE task_id=:tid AND student_id=:sid AND pipeline='L0_parse_resume' AND status='completed' "
        "AND JSON_EXTRACT(result_json, '$.confirmed')=true "
        "ON DUPLICATE KEY UPDATE "
        f"payload_json=IF({keep},personal_graph_projection.payload_json,VALUES(payload_json)), "
        f"status=IF({keep},personal_graph_projection.status,VALUES(status)), updated_at=CURRENT_TIMESTAMP(6)",
        {"tid": task_id, "sid": student_id, "status": state["status"], "state": json.dumps(state, ensure_ascii=False)}, commit=True)


def request_personal_graph_projection(mysql, *, student_id, task_id):
    """Call only AFTER confirmation commits. Durable state precedes delivery."""
    payload = _task(mysql, student_id, task_id)
    state = payload.get("personal_graph_projection") or {}
    if state.get("status") in TERMINAL:
        return state
    pending = {"status": "pending", "retryable": True, "source_task_id": task_id}
    _record(mysql, student_id, task_id, pending, preserve_terminal=True)
    job_id = "cpa-graph-" + hashlib.sha256((student_id + "\0" + task_id).encode()).hexdigest()
    try:
        projection_task.apply_async(args=[student_id, task_id], task_id=job_id, queue=GRAPH_QUEUE, retry=False)
    except Exception:
        pending = {**pending, "status": "retry_pending", "reason": "broker_unavailable"}
        _record(mysql, student_id, task_id, pending, preserve_terminal=True)
        return pending
    # Do not overwrite a fast worker's completed state with producer's queued
    # state. Persisted pending includes both not-yet-delivered and delivered work.
    return {**pending, "status": "queued"}


def execute_personal_graph_projection(*, student_id, task_id, mysql=None, neo4j=None):
    from .db import get_mysql_client, get_neo4j_client

    mysql = mysql or get_mysql_client()
    payload = _task(mysql, student_id, task_id)
    existing = payload.get("personal_graph_projection") or {}
    if existing.get("status") in TERMINAL:
        return existing
    try:
        result = publish_confirmed_personal_graph(mysql, neo4j or get_neo4j_client(),
            owner_student_id=student_id, expected_source_task_id=task_id)
        state = {"status": "completed", "retryable": False, "source_task_id": task_id,
                 "snapshot_id": result["snapshot_id"], "skill_count": result["skill_count"]}
    except PersonalGraphSuperseded:
        state = {"status": "superseded", "retryable": False, "source_task_id": task_id}
    except Exception:
        state = {"status": "retry_pending", "retryable": True, "source_task_id": task_id,
                 "reason": "projection_unavailable"}
    _record(mysql, student_id, task_id, state, preserve_terminal=True)
    return state


@celery_app.task(bind=True, name="cpa.graph.project", acks_late=True,
                 reject_on_worker_lost=True, ignore_result=True, max_retries=3)
def projection_task(self, student_id, task_id):
    result = execute_personal_graph_projection(student_id=student_id, task_id=task_id)
    if result["status"] == "retry_pending" and self.request.retries < self.max_retries:
        # Exhaustion leaves durable retry_pending for explicit journal replay;
        # no in-process retry thread or unbounded poison-message loop.
        raise self.retry(countdown=30 * (self.request.retries + 1))
    return result


def replay_personal_graph_projections(mysql, *, student_id=None, limit=50, after_id=0):
    """Admin/worker recovery entry; bounded scan, no global event cursor gaps.

    Replay all incomplete confirmed tasks, including a crash before dispatch.
    Event IDs are not used as an acknowledgement cursor: transaction commit order
    need not equal auto-increment order. The completed L0 row is the source even
    for older confirmations that predate journal-trigger installation.
    """
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be 1..200")
    if type(after_id) is not int or after_id < 0:
        raise ValueError("after_id must be nonnegative")
    owner_filter = " AND t.student_id=:sid" if student_id else ""
    rows = mysql.fetch_all(
        "SELECT t.id, t.task_id, t.student_id FROM pipeline_task t "
        "LEFT JOIN personal_graph_projection p ON p.student_id=t.student_id AND p.source_task_id=t.task_id "
        "WHERE t.id>:after_id AND t.pipeline='L0_parse_resume' AND t.status='completed' "
        "AND JSON_EXTRACT(t.result_json, '$.confirmed')=true "
        "AND COALESCE(p.status,'pending') NOT IN ('completed','superseded') " + owner_filter +
        " ORDER BY t.id LIMIT :lim", {"sid": student_id, "lim": limit + 1, "after_id": after_id})
    results = [request_personal_graph_projection(mysql, student_id=row["student_id"], task_id=row["task_id"])
               for row in rows[:limit]]
    return {"dispatched": len(results), "has_more": len(rows) > limit,
            "next_after_id": rows[limit-1]["id"] if len(rows) > limit else None, "results": results}


if __name__ == "__main__":
    import argparse
    from .db import get_mysql_client
    parser = argparse.ArgumentParser(description="Personal graph projection migration and durable replay")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--student-id")
    parser.add_argument("--after-id", type=int, default=0)
    args = parser.parse_args()
    mysql = get_mysql_client()
    if args.install:
        install_personal_graph_projection(mysql)
    if args.replay:
        print(json.dumps(replay_personal_graph_projections(mysql, student_id=args.student_id, after_id=args.after_id), ensure_ascii=False))
