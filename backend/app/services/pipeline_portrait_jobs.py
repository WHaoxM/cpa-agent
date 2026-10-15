"""Durable, owner-bound automatic L2 analysis after resume confirmation.

MySQL holds the intent and result. Celery transports only a task ID. This is
an adaptation of the existing L0/L3 dispatch protocol, not a new task engine.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import text

from .agent_runtime.media.jobs import PIPELINE_QUEUE, celery_app
from .db.mysql_client import get_mysql_client
from .pipeline import L2GeneratePortraitPipeline
from ..utils.logger import get_logger


logger = get_logger("cpa-agent.pipeline.portrait_jobs")
LEASE_SECONDS = 60 * 60
DISPATCH_DDL = """CREATE TABLE IF NOT EXISTS pipeline_portrait_dispatch (
    task_id VARCHAR(64) NOT NULL PRIMARY KEY,
    claim_token CHAR(32) DEFAULT NULL,
    lease_until DATETIME(6) DEFAULT NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_enqueued_at DATETIME(6) DEFAULT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_portrait_replay (lease_until, last_enqueued_at),
    CONSTRAINT fk_portrait_dispatch_task FOREIGN KEY (task_id)
        REFERENCES pipeline_task(task_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""


def install_portrait_dispatch(mysql):
    """Explicit additive migration, never issued by an HTTP request."""
    mysql.execute(DISPATCH_DDL, commit=True)
    rows = mysql.fetch_all(
        "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='pipeline_portrait_dispatch' "
        "AND CONSTRAINT_NAME='fk_portrait_dispatch_task' LIMIT 1")
    if not rows:
        mysql.execute(
            "ALTER TABLE pipeline_portrait_dispatch ADD CONSTRAINT fk_portrait_dispatch_task "
            "FOREIGN KEY (task_id) REFERENCES pipeline_task(task_id) ON DELETE CASCADE",
            commit=True)


def portrait_task_id(student_id: str, confirmation_task_id: str, self_report_revision: int = 0) -> str:
    digest = hashlib.sha256((student_id + "\0" + confirmation_task_id + "\0" +
                             str(self_report_revision) + "\0L2-auto-v1").encode()).hexdigest()
    return "task_" + digest[:32]


def persist_portrait_intent_tx(conn, *, student_id: str, confirmation_task_id: str,
                               self_report_revision: int = 0) -> str:
    """Called inside the same transaction that commits the L0 confirmation."""
    task_id = portrait_task_id(student_id, confirmation_task_id, self_report_revision)
    params = {"student_id": student_id,
              "source_confirmation_task_id": confirmation_task_id,
              "source_self_report_revision": self_report_revision}
    existing = conn.execute(text(
        "SELECT task_id,student_id,pipeline,params_json FROM pipeline_task "
        "WHERE task_id=:tid FOR UPDATE"), {"tid": task_id}).mappings().first()
    if existing:
        stored = existing["params_json"]
        stored = json.loads(stored) if isinstance(stored, str) else stored
        if (existing["student_id"] != student_id or existing["pipeline"] != "L2_generate_portrait"
                or stored != params):
            raise RuntimeError("画像任务的确定性引用与持久记录冲突")
        dispatch = conn.execute(text(
            "SELECT task_id FROM pipeline_portrait_dispatch WHERE task_id=:tid"),
            {"tid": task_id}).scalar_one_or_none()
        if dispatch != task_id:
            raise RuntimeError("画像任务缺少持久派发意图")
        return task_id
    conn.execute(text(
        "INSERT INTO pipeline_task(task_id,pipeline,status,params_json,student_id,triggered_by) "
        "VALUES (:tid,'L2_generate_portrait','pending',:params,:sid,'cron')"),
        {"tid": task_id, "sid": student_id,
         "params": json.dumps(params, ensure_ascii=False)})
    conn.execute(text("INSERT INTO pipeline_portrait_dispatch(task_id) VALUES (:tid)"),
                 {"tid": task_id})
    return task_id


def enqueue_portrait_intent(mysql, task_id: str) -> str:
    """Post-commit publish; Beat can replay a failed broker delivery."""
    try:
        row = mysql.fetch_one(
            "SELECT t.status FROM pipeline_task t JOIN pipeline_portrait_dispatch d "
            "ON d.task_id=t.task_id WHERE t.task_id=:tid AND t.pipeline='L2_generate_portrait'",
            {"tid": task_id})
        if not row or row["status"] not in ("pending", "running"):
            return "not_pending"
        mysql.execute("UPDATE pipeline_portrait_dispatch SET last_enqueued_at=NOW(6) "
                      "WHERE task_id=:tid", {"tid": task_id}, commit=True)
        portrait_task.apply_async(args=[task_id], task_id="cpa-portrait-" + task_id,
                                  queue=PIPELINE_QUEUE, retry=False)
    except Exception:
        logger.exception("L2 broker publish failed task_id=%s", task_id)
        return "retry_pending"
    return "queued"


def claim_portrait_intent(mysql, task_id: str):
    with mysql.engine.begin() as conn:
        row = conn.execute(text(
            "SELECT t.task_id,t.student_id,t.pipeline,t.status,t.params_json,"
            "d.claim_token,d.lease_until FROM pipeline_portrait_dispatch d "
            "JOIN pipeline_task t ON t.task_id=d.task_id WHERE d.task_id=:tid FOR UPDATE"),
            {"tid": task_id}).mappings().first()
        if not row or row["pipeline"] != "L2_generate_portrait" or row["status"] not in ("pending", "running"):
            return None
        now = conn.execute(text("SELECT NOW(6)")).scalar_one()
        if row["claim_token"] and (row["lease_until"] is None or row["lease_until"] >= now):
            return None
        params = row["params_json"]
        params = json.loads(params) if isinstance(params, str) else params
        if (not isinstance(params, dict) or set(params) != {"student_id", "source_confirmation_task_id",
                                                      "source_self_report_revision"}
                or params["student_id"] != row["student_id"]
                or not isinstance(params["source_confirmation_task_id"], str)
                or type(params["source_self_report_revision"]) is not int
                or params["source_self_report_revision"] < 0):
            raise RuntimeError("L2 持久任务输入与 owner 不一致")
        if row["status"] == "running":
            conn.execute(text("UPDATE pipeline_task SET status='pending',started_at=NULL "
                              "WHERE task_id=:tid AND status='running'"), {"tid": task_id})
        token = uuid.uuid4().hex
        conn.execute(text(
            "UPDATE pipeline_portrait_dispatch SET claim_token=:token,"
            "lease_until=NOW(6)+INTERVAL :seconds SECOND,attempts=attempts+1 "
            "WHERE task_id=:tid"),
            {"tid": task_id, "token": token, "seconds": LEASE_SECONDS})
        return {"task_id": task_id, "student_id": row["student_id"],
                "source_confirmation_task_id": params["source_confirmation_task_id"],
                "source_self_report_revision": params["source_self_report_revision"],
                "claim_token": token}


def execute_portrait_intent(task_id: str, *, mysql=None) -> dict:
    mysql = mysql or get_mysql_client()
    claim = claim_portrait_intent(mysql, task_id)
    if claim is None:
        return {"task_id": task_id, "status": "already_claimed_or_terminal"}
    runner = L2GeneratePortraitPipeline(task_id, {
        "student_id": claim["student_id"],
        "source_confirmation_task_id": claim["source_confirmation_task_id"],
        "source_self_report_revision": claim["source_self_report_revision"],
    }, mysql)
    runner.claim_token = claim["claim_token"]
    runner.dispatch_table = "pipeline_portrait_dispatch"
    try:
        return runner.run()
    except Exception:
        logger.exception("L2 worker execution stopped task_id=%s", task_id)
        row = mysql.fetch_one("SELECT status FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
        return {"task_id": task_id, "status": row["status"] if row else "missing"}


@celery_app.task(name="cpa.pipeline.portrait", acks_late=True,
                 reject_on_worker_lost=True, ignore_result=True)
def portrait_task(task_id: str):
    return execute_portrait_intent(task_id)


def replay_portrait_intents(mysql, *, limit=50, force=False):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be 1..200")
    due = "" if force else "AND (d.last_enqueued_at IS NULL OR d.last_enqueued_at<NOW(6)-INTERVAL 30 SECOND)"
    rows = mysql.fetch_all(
        "SELECT t.task_id FROM pipeline_task t JOIN pipeline_portrait_dispatch d ON d.task_id=t.task_id "
        "WHERE t.pipeline='L2_generate_portrait' AND t.status IN ('pending','running') "
        "AND (d.claim_token IS NULL OR d.lease_until<NOW(6)) " + due +
        " ORDER BY COALESCE(d.last_enqueued_at,CAST('1000-01-01' AS DATETIME)),t.task_id LIMIT :lim",
        {"lim": limit})
    return [{"task_id": row["task_id"], "delivery": enqueue_portrait_intent(mysql, row["task_id"])}
            for row in rows]


@celery_app.task(name="cpa.pipeline.portrait_replay", ignore_result=True)
def automatic_portrait_replay_task():
    return replay_portrait_intents(get_mysql_client(), limit=50)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="L2 durable portrait migration and replay")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    db = get_mysql_client()
    if args.install:
        install_portrait_dispatch(db)
    if args.replay:
        print(json.dumps(replay_portrait_intents(db, force=True), ensure_ascii=False))
