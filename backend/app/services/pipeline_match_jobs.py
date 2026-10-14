"""Durable, replayable L3 match dispatch. MySQL is the only result authority.

Only a task ID is sent through Celery. The owner, job and outcome are always
loaded from MySQL. Operators must explicitly run the additive migration before
enabling this path on an existing volume.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from .agent_runtime.media.jobs import PIPELINE_QUEUE, celery_app
from .db.mysql_client import get_mysql_client
from .pipeline import L3RunMatchPipeline
from ..utils.logger import get_logger

MATCH_QUEUE = PIPELINE_QUEUE
LEASE_SECONDS = 15 * 60
logger = get_logger("cpa-agent.pipeline.match_jobs")

DISPATCH_DDL = """CREATE TABLE IF NOT EXISTS pipeline_match_dispatch (
    task_id VARCHAR(64) NOT NULL PRIMARY KEY,
    claim_token CHAR(32) DEFAULT NULL,
    lease_until DATETIME(6) DEFAULT NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_enqueued_at DATETIME(6) DEFAULT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_match_replay (lease_until, last_enqueued_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""


def install_match_dispatch(mysql):
    """Explicit idempotent migration, never called in an HTTP request."""
    mysql.execute(DISPATCH_DDL, commit=True)


def persist_match_intent(mysql, *, student_id: str, job_portrait_id: str, task_id: str) -> None:
    """Commit one owner-bound task and its dispatch intent atomically."""
    params = json.dumps({"student_id": student_id, "job_id": job_portrait_id})
    with mysql.engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO pipeline_task(task_id,pipeline,status,params_json,student_id,job_id,triggered_by) "
            "VALUES (:tid,'L3_run_match','pending',:params,:sid,:jid,'api')"
        ), {"tid": task_id, "params": params, "sid": student_id, "jid": job_portrait_id})
        conn.execute(text("INSERT INTO pipeline_match_dispatch(task_id) VALUES (:tid)"), {"tid": task_id})


def enqueue_match_intent(mysql, task_id: str) -> str:
    """Publish after commit. A failure leaves the same intent for replay."""
    try:
        row = mysql.fetch_one(
            "SELECT t.status FROM pipeline_task t JOIN pipeline_match_dispatch d ON d.task_id=t.task_id "
            "WHERE t.task_id=:tid AND t.pipeline='L3_run_match'", {"tid": task_id})
    except Exception:
        # The caller may have just committed the task and dispatch intent.
        # Never turn a delivery-side read outage into an apparent rejection.
        logger.exception("L3 post-commit delivery read failed task_id=%s", task_id)
        return "unverified"
    if not row or row["status"] not in ("pending", "running"):
        return "not_pending"
    try:
        mysql.execute("UPDATE pipeline_match_dispatch SET last_enqueued_at=NOW(6) WHERE task_id=:tid",
                      {"tid": task_id}, commit=True)
    except Exception:
        # No broker publish follows an unconfirmed attempt stamp. After DB
        # recovery the same intent remains eligible for replay.
        logger.exception("L3 delivery attempt stamp failed task_id=%s", task_id)
        return "unverified"
    try:
        match_task.apply_async(args=[task_id], task_id="cpa-match-" + task_id,
                               queue=MATCH_QUEUE, retry=False)
    except Exception:
        # The durable row, not broker success, is the acceptance record.
        logger.exception("L3 broker publish failed task_id=%s", task_id)
        return "retry_pending"
    return "queued"


def claim_match_intent(mysql, task_id: str):
    """Atomic claim or stale-lease takeover; duplicate deliveries do no work."""
    with mysql.engine.begin() as conn:
        row = conn.execute(text(
            "SELECT t.task_id,t.pipeline,t.status,t.student_id,t.job_id,t.params_json, "
            "d.claim_token,d.lease_until "
            "FROM pipeline_match_dispatch d JOIN pipeline_task t ON t.task_id=d.task_id "
            "WHERE d.task_id=:tid FOR UPDATE"
        ), {"tid": task_id}).mappings().first()
        if not row or row["pipeline"] != "L3_run_match" or row["status"] not in ("pending", "running"):
            return None
        now = conn.execute(text("SELECT NOW(6)")).scalar_one()
        if row["claim_token"] and (row["lease_until"] is None or row["lease_until"] >= now):
            return None
        params = row["params_json"]
        params = json.loads(params) if isinstance(params, str) else params
        if (not isinstance(params, dict) or not row["student_id"] or not row["job_id"]
                or params != {"student_id": row["student_id"], "job_id": row["job_id"]}):
            raise RuntimeError("L3 持久任务输入与 owner 不一致")
        token = uuid.uuid4().hex
        if row["status"] == "running":
            conn.execute(text(
                "UPDATE pipeline_task SET status='pending',started_at=NULL "
                "WHERE task_id=:tid AND status='running'"), {"tid": task_id})
        conn.execute(text(
            "UPDATE pipeline_match_dispatch SET claim_token=:token, "
            "lease_until=NOW(6)+INTERVAL :seconds SECOND, attempts=attempts+1 "
            "WHERE task_id=:tid"
        ), {"token": token, "seconds": LEASE_SECONDS, "tid": task_id})
        return {"task_id": task_id, "student_id": row["student_id"],
                "job_id": row["job_id"], "claim_token": token}


def execute_match_intent(task_id: str, *, mysql=None) -> dict:
    mysql = mysql or get_mysql_client()
    claim = claim_match_intent(mysql, task_id)
    if claim is None:
        return {"status": "already_claimed_or_terminal", "task_id": task_id}
    runner = L3RunMatchPipeline(task_id, {"student_id": claim["student_id"],
                                          "job_id": claim["job_id"]}, mysql)
    runner.claim_token = claim["claim_token"]
    try:
        return runner.run()
    except Exception:
        # L3.run commits failed only while this claim still owns the task.
        # A setup crash or stale worker remains recoverable via the lease.
        logger.exception("L3 worker execution stopped task_id=%s", task_id)
        row = mysql.fetch_one("SELECT status FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
        return {"status": row["status"] if row else "missing", "task_id": task_id}


@celery_app.task(name="cpa.pipeline.match", acks_late=True,
                 reject_on_worker_lost=True, ignore_result=True)
def match_task(task_id: str):
    return execute_match_intent(task_id)


def replay_match_intents(mysql, *, student_id=None, limit=50, after_task_id="", force=True):
    """Bounded operator recovery for lost publish/message and expired workers."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be 1..200")
    owner_filter = " AND t.student_id=:sid" if student_id else ""
    due_filter = "" if force else (
        " AND (d.last_enqueued_at IS NULL OR d.last_enqueued_at<NOW(6)-INTERVAL 30 SECOND)"
    )
    ordering = ("t.task_id" if force else
                "COALESCE(d.last_enqueued_at, CAST('1000-01-01' AS DATETIME)) ASC, t.task_id")
    rows = mysql.fetch_all(
        "SELECT t.task_id FROM pipeline_task t JOIN pipeline_match_dispatch d ON d.task_id=t.task_id "
        "WHERE t.task_id>:after AND t.pipeline='L3_run_match' "
        "AND t.status IN ('pending','running') "
        "AND (d.claim_token IS NULL OR d.lease_until<NOW(6)) "
        + due_filter + owner_filter + " ORDER BY " + ordering + " LIMIT :lim",
        {"sid": student_id, "after": after_task_id, "lim": limit + 1})
    outcomes = [{"task_id": row["task_id"], "delivery": enqueue_match_intent(mysql, row["task_id"])}
                for row in rows[:limit]]
    return {"attempted": len(outcomes), "dispatched": sum(o["delivery"] == "queued" for o in outcomes),
            "has_more": len(rows) > limit,
            "next_after_task_id": rows[limit - 1]["task_id"] if len(rows) > limit else None,
            "results": outcomes}


@celery_app.task(name="cpa.pipeline.replay", acks_late=True,
                 reject_on_worker_lost=True, ignore_result=True)
def automatic_match_replay_task():
    """One bounded recovery page per Beat tick; oldest attempt first.

    The attempt stamp moves unavailable-broker rows behind untouched intents,
    so a fixed batch size cannot starve later owners in a large backlog.
    """
    return replay_match_intents(get_mysql_client(), limit=50, force=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="L3 durable match migration and replay")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--student-id")
    parser.add_argument("--after-task-id", default="")
    args = parser.parse_args()
    db = get_mysql_client()
    if args.install:
        install_match_dispatch(db)
    if args.replay:
        print(json.dumps(replay_match_intents(db, student_id=args.student_id,
                                              after_task_id=args.after_task_id), ensure_ascii=False))
