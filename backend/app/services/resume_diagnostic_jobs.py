"""Durable resume-only diagnostic generation after owner confirmation.

The report row, not a Celery result, is the business artifact. This uses the
existing pipeline_task journal and the same fenced MySQL/Celery dispatch shape
as L0/L3; report and portrait intents settle independently.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import text

from .agent_runtime.media.jobs import PIPELINE_QUEUE, celery_app
from .db.mysql_client import get_mysql_client
from ..utils.logger import get_logger


logger = get_logger("cpa-agent.resume_diagnostic_jobs")
LEASE_SECONDS = 60 * 60
DISPATCH_DDL = """CREATE TABLE IF NOT EXISTS resume_diagnostic_dispatch (
    task_id VARCHAR(64) NOT NULL PRIMARY KEY,
    claim_token CHAR(32) DEFAULT NULL,
    lease_until DATETIME(6) DEFAULT NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_enqueued_at DATETIME(6) DEFAULT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_diagnostic_replay (lease_until, last_enqueued_at),
    CONSTRAINT fk_diagnostic_dispatch_task FOREIGN KEY (task_id)
        REFERENCES pipeline_task(task_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""


def install_diagnostic_dispatch(mysql):
    """Explicit additive migration, never from a request thread."""
    mysql.execute(DISPATCH_DDL, commit=True)


def diagnostic_task_id(student_id: str, confirmation_task_id: str) -> str:
    digest = hashlib.sha256((student_id + "\0" + confirmation_task_id + "\0R1-auto-v1").encode()).hexdigest()
    return "task_" + digest[:32]


def persist_diagnostic_intent_tx(conn, *, student_id: str, confirmation_task_id: str) -> str:
    task_id = diagnostic_task_id(student_id, confirmation_task_id)
    params = {"student_id": student_id,
              "source_confirmation_task_id": confirmation_task_id}
    existing = conn.execute(text(
        "SELECT task_id,student_id,pipeline,params_json FROM pipeline_task "
        "WHERE task_id=:tid FOR UPDATE"), {"tid": task_id}).mappings().first()
    if existing:
        stored = existing["params_json"]
        stored = json.loads(stored) if isinstance(stored, str) else stored
        if (existing["student_id"] != student_id or existing["pipeline"] != "R1_resume_diagnostic"
                or stored != params):
            raise RuntimeError("诊断任务的确定性引用与持久记录冲突")
        dispatch = conn.execute(text(
            "SELECT task_id FROM resume_diagnostic_dispatch WHERE task_id=:tid"),
            {"tid": task_id}).scalar_one_or_none()
        if dispatch != task_id:
            raise RuntimeError("诊断任务缺少持久派发意图")
        return task_id
    conn.execute(text(
        "INSERT INTO pipeline_task(task_id,pipeline,status,params_json,student_id,triggered_by) "
        "VALUES (:tid,'R1_resume_diagnostic','pending',:params,:sid,'api')"),
        {"tid": task_id, "sid": student_id,
         "params": json.dumps(params, ensure_ascii=False)})
    conn.execute(text("INSERT INTO resume_diagnostic_dispatch(task_id) VALUES (:tid)"),
                 {"tid": task_id})
    return task_id


def enqueue_diagnostic_intent(mysql, task_id: str) -> str:
    try:
        row = mysql.fetch_one(
            "SELECT t.status FROM pipeline_task t JOIN resume_diagnostic_dispatch d "
            "ON d.task_id=t.task_id WHERE t.task_id=:tid AND t.pipeline='R1_resume_diagnostic'",
            {"tid": task_id})
        if not row or row["status"] not in ("pending", "running"):
            return "not_pending"
        mysql.execute("UPDATE resume_diagnostic_dispatch SET last_enqueued_at=NOW(6) "
                      "WHERE task_id=:tid", {"tid": task_id}, commit=True)
        diagnostic_task.apply_async(args=[task_id], task_id="cpa-diagnostic-" + task_id,
                                    queue=PIPELINE_QUEUE, retry=False)
    except Exception:
        logger.exception("diagnostic broker publish failed task_id=%s", task_id)
        return "retry_pending"
    return "queued"


def claim_diagnostic_intent(mysql, task_id: str):
    with mysql.engine.begin() as conn:
        row = conn.execute(text(
            "SELECT t.task_id,t.student_id,t.pipeline,t.status,t.params_json,"
            "d.claim_token,d.lease_until FROM resume_diagnostic_dispatch d "
            "JOIN pipeline_task t ON t.task_id=d.task_id WHERE d.task_id=:tid FOR UPDATE"),
            {"tid": task_id}).mappings().first()
        if not row or row["pipeline"] != "R1_resume_diagnostic" or row["status"] not in ("pending", "running"):
            return None
        now = conn.execute(text("SELECT NOW(6)")).scalar_one()
        if row["claim_token"] and (row["lease_until"] is None or row["lease_until"] >= now):
            return None
        params = row["params_json"]
        params = json.loads(params) if isinstance(params, str) else params
        if (not isinstance(params, dict) or set(params) != {"student_id", "source_confirmation_task_id"}
                or params["student_id"] != row["student_id"]
                or not isinstance(params["source_confirmation_task_id"], str)):
            raise RuntimeError("诊断任务输入与 owner 不一致")
        if row["status"] == "running":
            conn.execute(text("UPDATE pipeline_task SET status='pending',started_at=NULL "
                              "WHERE task_id=:tid AND status='running'"), {"tid": task_id})
        token = uuid.uuid4().hex
        conn.execute(text(
            "UPDATE resume_diagnostic_dispatch SET claim_token=:token,"
            "lease_until=NOW(6)+INTERVAL :seconds SECOND,attempts=attempts+1 "
            "WHERE task_id=:tid"),
            {"tid": task_id, "token": token, "seconds": LEASE_SECONDS})
        return {"task_id": task_id, "student_id": row["student_id"],
                "source_confirmation_task_id": params["source_confirmation_task_id"],
                "claim_token": token}


def execute_diagnostic_intent(task_id: str, *, mysql=None, llm_client=None) -> dict:
    mysql = mysql or get_mysql_client()
    claim = claim_diagnostic_intent(mysql, task_id)
    if claim is None:
        return {"task_id": task_id, "status": "already_claimed_or_terminal"}
    fence = {"task_id": task_id, "student_id": claim["student_id"],
             "claim_token": claim["claim_token"]}
    try:
        with mysql.engine.begin() as conn:
            changed = conn.execute(text(
                "UPDATE pipeline_task SET status='running',started_at=NOW() "
                "WHERE task_id=:task_id AND student_id=:student_id AND status='pending' "
                "AND EXISTS (SELECT 1 FROM resume_diagnostic_dispatch d "
                "WHERE d.task_id=pipeline_task.task_id AND d.claim_token=:claim_token)"), fence)
            if changed.rowcount != 1:
                raise RuntimeError("诊断任务领取状态已变化")
        from .pipeline import require_confirmed_profile
        profile = require_confirmed_profile(mysql, claim["student_id"])
        if profile["_resume_confirmation"]["task_id"] != claim["source_confirmation_task_id"]:
            raise ValueError("诊断任务源于旧版已确认简历")
        from .resume_diagnostic_report import create_or_read_resume_diagnostic
        report = create_or_read_resume_diagnostic(
            mysql, student_id=claim["student_id"], llm_client=llm_client,
            expected_confirmation_task_id=claim["source_confirmation_task_id"],
            writer_claim=fence)
        status = report["status"]
        if status not in {"completed", "partial"}:
            raise RuntimeError("诊断报告未形成可回读结果")
        result = {"task_id": task_id, "student_id": claim["student_id"],
                  "source_confirmation_task_id": claim["source_confirmation_task_id"],
                  "report_id": report["report_id"], "status": status,
                  "missing_sections": report.get("missing_sections") or []}
        with mysql.engine.begin() as conn:
            changed = conn.execute(text(
                "UPDATE pipeline_task SET status=:status,completed_at=NOW(),result_json=:result "
                "WHERE task_id=:task_id AND student_id=:student_id AND status='running' "
                "AND EXISTS (SELECT 1 FROM resume_diagnostic_dispatch d "
                "WHERE d.task_id=pipeline_task.task_id AND d.claim_token=:claim_token)"),
                {**fence, "status": status, "result": json.dumps(result, ensure_ascii=False)})
            if changed.rowcount != 1:
                raise RuntimeError("诊断任务完成状态被过期执行者拒绝")
        return result
    except Exception:
        logger.exception("diagnostic worker execution stopped task_id=%s", task_id)
        with mysql.engine.begin() as conn:
            conn.execute(text(
                "UPDATE pipeline_task SET status='failed',error_message='诊断报告生成失败，可重试' "
                "WHERE task_id=:task_id AND student_id=:student_id AND status='running' "
                "AND EXISTS (SELECT 1 FROM resume_diagnostic_dispatch d "
                "WHERE d.task_id=pipeline_task.task_id AND d.claim_token=:claim_token)"), fence)
        return {"task_id": task_id, "status": "failed"}


@celery_app.task(name="cpa.pipeline.diagnostic", acks_late=True,
                 reject_on_worker_lost=True, ignore_result=True)
def diagnostic_task(task_id: str):
    return execute_diagnostic_intent(task_id)


def replay_diagnostic_intents(mysql, *, limit=50, force=False):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be 1..200")
    due = "" if force else "AND (d.last_enqueued_at IS NULL OR d.last_enqueued_at<NOW(6)-INTERVAL 30 SECOND)"
    rows = mysql.fetch_all(
        "SELECT t.task_id FROM pipeline_task t JOIN resume_diagnostic_dispatch d ON d.task_id=t.task_id "
        "WHERE t.pipeline='R1_resume_diagnostic' AND t.status IN ('pending','running') "
        "AND (d.claim_token IS NULL OR d.lease_until<NOW(6)) " + due +
        " ORDER BY COALESCE(d.last_enqueued_at,CAST('1000-01-01' AS DATETIME)),t.task_id LIMIT :lim",
        {"lim": limit})
    return [{"task_id": row["task_id"], "delivery": enqueue_diagnostic_intent(mysql, row["task_id"])}
            for row in rows]


@celery_app.task(name="cpa.pipeline.diagnostic_replay", ignore_result=True)
def automatic_diagnostic_replay_task():
    return replay_diagnostic_intents(get_mysql_client(), limit=50)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Resume diagnostic durable dispatch migration")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    db = get_mysql_client()
    if args.install:
        install_diagnostic_dispatch(db)
    if args.replay:
        print(json.dumps(replay_diagnostic_intents(db, force=True), ensure_ascii=False))
