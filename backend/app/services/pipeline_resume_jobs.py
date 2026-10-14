"""Durable L0 resume dispatch and checkpoint trace.

The MySQL task is authoritative; Celery only delivers a task ID. Percentages
count completed, persisted checkpoints, not elapsed-time predictions.
"""

from __future__ import annotations

import json
import os
import uuid

from sqlalchemy import text

from .agent_runtime.media.jobs import PIPELINE_QUEUE, celery_app
from .db.mysql_client import get_mysql_client
from .pipeline import L0ParseResumePipeline
from ..utils.logger import get_logger

logger = get_logger("cpa-agent.pipeline.resume_jobs")
LEASE_SECONDS = 60 * 60

DISPATCH_DDL = """CREATE TABLE IF NOT EXISTS pipeline_resume_dispatch (
    task_id VARCHAR(64) NOT NULL PRIMARY KEY,
    claim_token CHAR(32) DEFAULT NULL,
    lease_until DATETIME(6) DEFAULT NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_enqueued_at DATETIME(6) DEFAULT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_resume_replay (lease_until, last_enqueued_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""

TRACE_DDL = """CREATE TABLE IF NOT EXISTS pipeline_task_trace (
    event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    student_id VARCHAR(64) NOT NULL,
    stage VARCHAR(40) NOT NULL,
    state VARCHAR(20) NOT NULL,
    progress TINYINT UNSIGNED NOT NULL,
    source_name VARCHAR(60) NOT NULL,
    target_name VARCHAR(60) NOT NULL,
    facts_json JSON DEFAULT NULL,
    occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_resume_trace_cursor (task_id, event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""

STAGE_LABELS = {
    "file_received": ("Browser", "DocumentAgent"),
    "attempt_started": ("ResumePipeline", "DocumentAgent"),
    "source_extract": ("ResumePipeline", "DocumentAgent"),
    "source_extracted": ("DocumentAgent", "ResumePipeline"),
    "handoff_document": ("DocumentAgent", "StructureAgent"),
    "structure_extract": ("ResumePipeline", "StructureAgent"),
    "structure_model": ("StructureAgent", "ResumeModel"),
    "structure_extracted": ("StructureAgent", "ResumePipeline"),
    "handoff_structure": ("StructureAgent", "EntityAgent"),
    "entity_extract": ("ResumePipeline", "EntityAgent"),
    "mention_model": ("EntityAgent", "ResumeModel"),
    "link_model": ("EntityAgent", "ResumeModel"),
    "entity_resolved": ("EntityAgent", "ResumePipeline"),
    "handoff_entity": ("EntityAgent", "EvidenceAgent"),
    "evidence_assemble": ("ResumePipeline", "EvidenceAgent"),
    "evidence_assembled": ("EvidenceAgent", "CandidateStore"),
    "candidate_saved": ("CandidateStore", "StudentReview"),
    "failed": ("ResumePipeline", "StudentReview"),
}
STAGE_KIND = {
    "file_received": "input", "attempt_started": "attempt", "source_extract": "tool_call",
    "source_extracted": "tool_result", "handoff_document": "handoff",
    "structure_extract": "agent_run",
    "structure_model": "model_call", "mention_model": "model_call",
    "link_model": "model_call", "structure_extracted": "agent_result",
    "handoff_structure": "handoff", "entity_extract": "agent_run",
    "entity_resolved": "agent_result", "handoff_entity": "handoff",
    "evidence_assemble": "agent_run",
    "evidence_assembled": "handoff", "candidate_saved": "artifact",
    "failed": "error",
}
STAGE_ACTOR = {
    "file_received": "UploadGateway", "attempt_started": "ResumePipeline",
    "source_extract": "DocumentAgent",
    "source_extracted": "DocumentAgent", "handoff_document": "DocumentAgent",
    "structure_extract": "StructureAgent", "structure_model": "StructureAgent",
    "structure_extracted": "StructureAgent", "handoff_structure": "StructureAgent",
    "entity_extract": "EntityAgent", "mention_model": "EntityAgent",
    "link_model": "EntityAgent", "entity_resolved": "EntityAgent",
    "handoff_entity": "EntityAgent", "evidence_assemble": "EvidenceAgent",
    "evidence_assembled": "EvidenceAgent", "candidate_saved": "CandidateStore",
    "failed": "ResumePipeline",
}


def install_resume_dispatch(mysql):
    """Explicit additive migration. Never run DDL in an HTTP request."""
    with mysql.engine.begin() as conn:
        conn.execute(text(DISPATCH_DDL))
        conn.execute(text(TRACE_DDL))


def _insert_trace(conn, *, task_id, student_id, stage, state, progress, facts=None):
    source, target = STAGE_LABELS[stage]
    conn.execute(text(
        "INSERT INTO pipeline_task_trace "
        "(task_id,student_id,stage,state,progress,source_name,target_name,facts_json) "
        "VALUES (:tid,:sid,:stage,:state,:progress,:source,:target,:facts)"
    ), {"tid": task_id, "sid": student_id, "stage": stage, "state": state,
        "progress": progress, "source": source, "target": target,
        "facts": json.dumps(facts or {}, ensure_ascii=False)})


def persist_resume_intent(mysql, *, student_id, file_path, task_id):
    """Create owner-bound task, dispatch intent, and upload receipt together."""
    params = json.dumps({"student_id": student_id, "file_path": file_path})
    with mysql.engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO pipeline_task(task_id,pipeline,status,progress,params_json,student_id,triggered_by) "
            "VALUES (:tid,'L0_parse_resume','pending',0,:params,:sid,'api')"
        ), {"tid": task_id, "sid": student_id, "params": params})
        conn.execute(text("INSERT INTO pipeline_resume_dispatch(task_id) VALUES (:tid)"), {"tid": task_id})
        _insert_trace(conn, task_id=task_id, student_id=student_id,
                      stage="file_received", state="completed", progress=0,
                      facts={"file_size_bytes": os.path.getsize(file_path) if os.path.isfile(file_path) else None})


def record_resume_stage(mysql, *, task_id, student_id, claim_token, stage, state, progress, facts=None):
    """Persist a whitelist-only handoff and exact completed-checkpoint count."""
    if stage not in STAGE_LABELS or not 0 <= progress <= 100:
        raise ValueError("invalid resume trace checkpoint")
    with mysql.engine.begin() as conn:
        changed = conn.execute(text(
            "UPDATE pipeline_task SET progress=:progress WHERE task_id=:tid AND student_id=:sid "
            "AND status='running' AND EXISTS (SELECT 1 FROM pipeline_resume_dispatch d "
            "WHERE d.task_id=pipeline_task.task_id AND d.claim_token=:token)"
        ), {"tid": task_id, "sid": student_id, "token": claim_token,
            "progress": progress})
        if changed.rowcount != 1:
            raise RuntimeError("简历任务阶段记录被过期执行者拒绝")
        _insert_trace(conn, task_id=task_id, student_id=student_id, stage=stage,
                      state=state, progress=progress, facts=facts)


def enqueue_resume_intent(mysql, task_id):
    """Post-commit publish; failed delivery stays replayable."""
    try:
        mysql.execute("UPDATE pipeline_resume_dispatch SET last_enqueued_at=NOW(6) "
                      "WHERE task_id=:tid", {"tid": task_id}, commit=True)
        resume_task.apply_async(args=[task_id], task_id="cpa-resume-" + task_id,
                                queue=PIPELINE_QUEUE, retry=False)
    except Exception:
        logger.exception("L0 broker publish failed task_id=%s", task_id)
        return "retry_pending"
    return "queued"


def claim_resume_intent(mysql, task_id):
    with mysql.engine.begin() as conn:
        row = conn.execute(text(
            "SELECT t.task_id,t.pipeline,t.status,t.student_id,t.params_json,"
            "d.claim_token,d.lease_until,d.attempts FROM pipeline_resume_dispatch d "
            "JOIN pipeline_task t ON t.task_id=d.task_id WHERE d.task_id=:tid FOR UPDATE"
        ), {"tid": task_id}).mappings().first()
        if not row or row["pipeline"] != "L0_parse_resume" or row["status"] not in ("pending", "running"):
            return None
        now = conn.execute(text("SELECT NOW(6)")).scalar_one()
        if row["claim_token"] and (row["lease_until"] is None or row["lease_until"] >= now):
            return None
        params = json.loads(row["params_json"]) if isinstance(row["params_json"], str) else row["params_json"]
        if (not isinstance(params, dict) or
                params.get("student_id") != row["student_id"] or
                not isinstance(params.get("file_path"), str)):
            raise RuntimeError("L0 持久任务输入与 owner 不一致")
        if row["status"] == "running":
            conn.execute(text("UPDATE pipeline_task SET status='pending',progress=0,started_at=NULL "
                              "WHERE task_id=:tid AND status='running'"), {"tid": task_id})
        token = uuid.uuid4().hex
        conn.execute(text(
            "UPDATE pipeline_resume_dispatch SET claim_token=:token,"
            "lease_until=NOW(6)+INTERVAL :seconds SECOND,attempts=attempts+1 "
            "WHERE task_id=:tid"
        ), {"tid": task_id, "token": token, "seconds": LEASE_SECONDS})
        attempt = row["attempts"] + 1
        _insert_trace(conn, task_id=task_id, student_id=row["student_id"],
                      stage="attempt_started", state="running", progress=0,
                      facts={"attempt": attempt, "recovered": attempt > 1})
        return {"task_id": task_id, "student_id": row["student_id"],
                "file_path": params["file_path"], "claim_token": token,
                "attempt": attempt}


def execute_resume_intent(task_id, *, mysql=None):
    mysql = mysql or get_mysql_client()
    claim = claim_resume_intent(mysql, task_id)
    if claim is None:
        return {"task_id": task_id, "status": "already_claimed_or_terminal"}
    runner = L0ParseResumePipeline(task_id, {"student_id": claim["student_id"],
                                             "file_path": claim["file_path"]}, mysql)
    runner.claim_token = claim["claim_token"]
    runner.dispatch_table = "pipeline_resume_dispatch"
    runner.trace_enabled = True
    try:
        return runner.run()
    except Exception:
        logger.exception("L0 worker execution stopped task_id=%s", task_id)
        row = mysql.fetch_one("SELECT status FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
        return {"task_id": task_id, "status": row["status"] if row else "missing"}


@celery_app.task(name="cpa.pipeline.resume", acks_late=True,
                 reject_on_worker_lost=True, ignore_result=True)
def resume_task(task_id):
    return execute_resume_intent(task_id)


@celery_app.task(name="cpa.pipeline.resume_replay", ignore_result=True)
def automatic_resume_replay_task():
    return replay_resume_intents(get_mysql_client(), limit=50)


def replay_resume_intents(mysql, *, limit=50, force=False):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be 1..200")
    due = "" if force else "AND (d.last_enqueued_at IS NULL OR d.last_enqueued_at<NOW(6)-INTERVAL 30 SECOND)"
    rows = mysql.fetch_all(
        "SELECT t.task_id FROM pipeline_task t JOIN pipeline_resume_dispatch d ON d.task_id=t.task_id "
        "WHERE t.pipeline='L0_parse_resume' AND t.status IN ('pending','running') "
        "AND (d.claim_token IS NULL OR d.lease_until<NOW(6)) " + due +
        " ORDER BY COALESCE(d.last_enqueued_at,CAST('1000-01-01' AS DATETIME)),t.task_id LIMIT :lim",
        {"lim": limit})
    return [{"task_id": row["task_id"], "delivery": enqueue_resume_intent(mysql, row["task_id"])}
            for row in rows]


def read_resume_trace(mysql, *, student_id, task_id, cursor=0, limit=100):
    if cursor < 0 or not 1 <= limit <= 200:
        raise ValueError("Invalid cursor or limit")
    with mysql.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            task = conn.execute(text(
                "SELECT t.task_id,t.pipeline,t.status,t.progress,"
                "COALESCE(d.attempts,0) AS current_attempt "
                "FROM pipeline_task t LEFT JOIN pipeline_resume_dispatch d ON d.task_id=t.task_id "
                "WHERE t.task_id=:tid AND t.student_id=:sid AND t.pipeline='L0_parse_resume'"
            ), {"tid": task_id, "sid": student_id}).mappings().first()
            if task is None:
                raise LookupError("Resume task not found")
            # Cursor pages must retain attempt context. Older trace rows before
            # the first marker cannot be assigned to a past attempt reliably.
            previous_marker = conn.execute(text(
                "SELECT facts_json FROM pipeline_task_trace "
                "WHERE task_id=:tid AND student_id=:sid AND stage='attempt_started' "
                "AND event_id<=:cursor ORDER BY event_id DESC LIMIT 1"
            ), {"tid": task_id, "sid": student_id, "cursor": cursor}).scalar_one_or_none()
            previous_facts = json.loads(previous_marker) if isinstance(previous_marker, str) else previous_marker
            attempt = previous_facts.get("attempt") if isinstance(previous_facts, dict) else None
            rows = list(conn.execute(text(
                "SELECT event_id,stage,state,progress,source_name,target_name,facts_json,occurred_at "
                "FROM pipeline_task_trace WHERE task_id=:tid AND student_id=:sid AND event_id>:cursor "
                "ORDER BY event_id LIMIT :lim"
            ), {"tid": task_id, "sid": student_id, "cursor": cursor,
                "lim": limit + 1}).mappings())
            events = []
            for row in rows[:limit]:
                facts = json.loads(row["facts_json"]) if isinstance(row["facts_json"], str) else row["facts_json"] or {}
                if row["stage"] == "attempt_started":
                    attempt = facts.get("attempt") if isinstance(facts, dict) else None
                event_attempt = None if row["stage"] == "file_received" else attempt
                events.append({"event_id": row["event_id"], "stage": row["stage"],
                               "state": row["state"], "progress": row["progress"],
                               "attempt": event_attempt,
                               "kind": STAGE_KIND.get(row["stage"], "handoff"),
                               "actor": STAGE_ACTOR.get(row["stage"], "ResumePipeline"),
                               "source": row["source_name"], "target": row["target_name"],
                               "facts": facts, "occurred_at": row["occurred_at"].isoformat()})
            return {"task": dict(task), "events": events,
                    "cursor": events[-1]["event_id"] if events else cursor,
                    "has_more": len(rows) > limit,
                    "progress_basis": "4_completed_checkpoints_not_elapsed_time"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    db = get_mysql_client()
    if args.install:
        install_resume_dispatch(db)
    if args.replay:
        print(json.dumps(replay_resume_intents(db, force=True), ensure_ascii=False))
