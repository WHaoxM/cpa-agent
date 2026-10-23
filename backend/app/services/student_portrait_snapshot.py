"""Immutable owner-bound portrait results, separate from scored L3/L4 rows.

The old student_portrait_4d table remains the scoring gate. A snapshot is a
readable analysis artifact even when one or more dimensions are unknown.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from .job_publication import canonical_json, content_sha256


SNAPSHOT_DDL = """CREATE TABLE IF NOT EXISTS student_portrait_snapshot (
    snapshot_id VARCHAR(64) NOT NULL PRIMARY KEY,
    student_id VARCHAR(64) NOT NULL,
    source_task_id VARCHAR(64) NOT NULL,
    source_confirmation_task_id VARCHAR(64) NOT NULL,
    source_self_report_revision INT NOT NULL,
    target_job_id VARCHAR(64) DEFAULT NULL,
    status VARCHAR(20) NOT NULL,
    portrait_json JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uk_portrait_snapshot_task (source_task_id),
    KEY idx_portrait_snapshot_owner (student_id, created_at, snapshot_id),
    CONSTRAINT fk_portrait_snapshot_task FOREIGN KEY (source_task_id)
        REFERENCES pipeline_task(task_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""


class PortraitSnapshotInvalid(ValueError):
    """A persisted snapshot does not match its task, hash or owner."""


def install_portrait_snapshot(mysql):
    """Explicit local installation; managed deployments use Alembic 0007."""
    mysql.execute(SNAPSHOT_DDL, commit=True)


def snapshot_id_for_task(task_id: str) -> str:
    return "ps_" + hashlib.sha256((task_id + "\0portrait-v1").encode()).hexdigest()[:32]


def persist_portrait_snapshot_tx(conn, *, student_id: str, source_task_id: str,
                                 portrait: dict, status: str, target_job_id: str | None) -> str:
    if status not in {"partial", "completed"} or not isinstance(portrait, dict):
        raise ValueError("画像快照必须来自已结算的画像任务")
    if portrait.get("source_portrait_task_id") != source_task_id:
        raise ValueError("画像快照的任务来源不一致")
    confirmation_id = portrait.get("source_confirmation_task_id")
    revision = portrait.get("source_self_report_revision")
    if (not isinstance(confirmation_id, str) or not confirmation_id
            or type(revision) is not int or revision < 0):
        raise ValueError("画像快照的简历或自述版本无效")
    sid = snapshot_id_for_task(source_task_id)
    digest = content_sha256(portrait)
    existing = conn.execute(text(
        "SELECT snapshot_id,student_id,source_confirmation_task_id,"
        "source_self_report_revision,target_job_id,status,content_sha256 "
        "FROM student_portrait_snapshot WHERE source_task_id=:task_id FOR UPDATE"),
        {"task_id": source_task_id}).mappings().first()
    if existing:
        if (existing["snapshot_id"] != sid or existing["student_id"] != student_id
                or existing["source_confirmation_task_id"] != confirmation_id
                or existing["source_self_report_revision"] != revision
                or existing["target_job_id"] != target_job_id
                or existing["status"] != status or existing["content_sha256"] != digest):
            raise PortraitSnapshotInvalid("已保存的画像快照与本次任务冲突")
        return sid
    conn.execute(text(
        "INSERT INTO student_portrait_snapshot "
        "(snapshot_id,student_id,source_task_id,source_confirmation_task_id,"
        "source_self_report_revision,target_job_id,status,portrait_json,content_sha256) "
        "VALUES (:snapshot_id,:student_id,:source_task_id,:confirmation_id,"
        ":revision,:target_job_id,:status,:portrait_json,:digest)"),
        {"snapshot_id": sid, "student_id": student_id, "source_task_id": source_task_id,
         "confirmation_id": confirmation_id, "revision": revision,
         "target_job_id": target_job_id, "status": status,
         "portrait_json": canonical_json(portrait), "digest": digest})
    return sid


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _validated(row, *, student_id: str) -> dict:
    if not row or row["student_id"] != student_id:
        raise LookupError("画像快照不存在")
    portrait = _decode(row["portrait_json"])
    result = _decode(row["result_json"])
    params = _decode(row["params_json"])
    if (not isinstance(portrait, dict) or not isinstance(result, dict)
            or not isinstance(params, dict)
            or row["snapshot_id"] != snapshot_id_for_task(row["source_task_id"])
            or row["source_task_owner"] != student_id
            or row["pipeline"] != "L2_generate_portrait"
            or row["task_status"] != row["status"]
            or row["status"] not in {"partial", "completed"}
            or row["source_confirmation_task_id"] != portrait.get("source_confirmation_task_id")
            or row["source_self_report_revision"] != portrait.get("source_self_report_revision")
            or row["source_task_id"] != portrait.get("source_portrait_task_id")
            or row["target_job_id"] != params.get("job_id")
            or row["content_sha256"] != content_sha256(portrait)
            or result.get("snapshot_id") != row["snapshot_id"]
            or result.get("status") != row["status"]
            or (result.get("portrait_candidate") if row["status"] == "partial"
                else result.get("portrait")) != portrait):
        raise PortraitSnapshotInvalid("画像快照未通过来源或内容校验")
    return {"snapshot_id": row["snapshot_id"], "student_id": student_id,
            "source_task_id": row["source_task_id"],
            "source_confirmation_task_id": row["source_confirmation_task_id"],
            "source_self_report_revision": row["source_self_report_revision"],
            "target_job_id": row["target_job_id"], "status": row["status"],
            "portrait": portrait, "created_at": row["created_at"].isoformat()}


_SELECT = (
    "SELECT s.*,t.student_id AS source_task_owner,t.pipeline,t.status AS task_status,"
    "t.result_json,t.params_json FROM student_portrait_snapshot s "
    "JOIN pipeline_task t ON t.task_id=s.source_task_id "
)


def read_portrait_snapshot(mysql, *, student_id: str, snapshot_id: str) -> dict:
    rows = mysql.fetch_all(_SELECT + "WHERE s.snapshot_id=:snapshot_id AND s.student_id=:sid LIMIT 1",
                           {"snapshot_id": snapshot_id, "sid": student_id})
    return _validated(rows[0] if rows else None, student_id=student_id)


def read_snapshot_for_task(mysql, *, student_id: str, source_task_id: str) -> dict | None:
    rows = mysql.fetch_all(_SELECT + "WHERE s.source_task_id=:task_id AND s.student_id=:sid LIMIT 1",
                           {"task_id": source_task_id, "sid": student_id})
    return _validated(rows[0], student_id=student_id) if rows else None


def list_portrait_snapshots(mysql, *, student_id: str, limit: int = 20, offset: int = 0) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("画像历史条数无效")
    if type(offset) is not int or offset < 0:
        raise ValueError("画像历史偏移无效")
    rows = mysql.fetch_all(_SELECT + "WHERE s.student_id=:sid AND s.target_job_id IS NULL "
                           "ORDER BY s.created_at DESC,s.snapshot_id DESC LIMIT :lim OFFSET :off",
                           {"sid": student_id, "lim": limit, "off": offset})
    return [_validated(row, student_id=student_id) for row in rows]


if __name__ == "__main__":
    import argparse
    from .db.mysql_client import get_mysql_client
    parser = argparse.ArgumentParser(description="Install local portrait snapshot table")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if args.install:
        install_portrait_snapshot(get_mysql_client())
