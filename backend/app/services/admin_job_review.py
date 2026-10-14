"""Account-admin read adapter for immutable model job-source audits.

Model output is untrusted review material, never publication authorization.
This adapter only reads exact stored rows and checks their content digest.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from .job_publication import content_sha256, scoring_readiness, validate_snapshot

_MODEL_ONLY_CANDIDATE_FIELDS = frozenset({
    "reviewed_by", "reviewed_at", "review_state", "review_actor", "review_id",
})
_REVIEW_STATUSES = frozenset({"blocked", "candidate_ready"})


class JobReviewCorrupt(ValueError):
    pass


def _checked(row):
    try:
        review = row["review_json"]
        review = json.loads(review) if isinstance(review, str) else review
        if not isinstance(review, dict) or content_sha256(review) != row["content_sha256"]:
            raise JobReviewCorrupt("岗位模型审查摘要不一致")
        for field in ("review_id", "source_record_id", "review_version", "actor", "status"):
            if review.get(field) != row[field]:
                raise JobReviewCorrupt("岗位模型审查索引与原文不一致")
        if (row["actor"] != "model" or row["status"] not in _REVIEW_STATUSES
                or review.get("publication_authorized") is not False
                or not isinstance(review.get("blockers"), list)
                or any(not isinstance(item, str) for item in review["blockers"])):
            raise JobReviewCorrupt("岗位模型审查状态无效")
        if row["status"] == "candidate_ready" and (
            review.get("scoring_readiness") is not True or review["blockers"]
        ):
            raise JobReviewCorrupt("模型候选状态与门禁结果冲突")
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise JobReviewCorrupt("岗位模型审查记录无法校验") from exc
    return review


def _summary(row, review):
    return {
        "review_id": row["review_id"],
        "source_record_id": row["source_record_id"],
        "review_version": row["review_version"],
        "actor": "model",
        "model": review.get("model"),
        "status": row["status"],
        "blockers": review["blockers"],
        "scoring_readiness": review.get("scoring_readiness") is True,
        "publication_authorized": False,
        "content_sha256": row["content_sha256"],
        "created_at": row["created_at"].isoformat(),
        "source_current": "unknown",
    }


def list_admin_job_reviews(mysql, *, status=None, limit=20, offset=0):
    if status is not None and status not in _REVIEW_STATUSES:
        raise ValueError("status 无效")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 无效")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 无效")
    where = " WHERE status=:status" if status else ""
    params = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    with mysql.engine.begin() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM job_catalog_review" + where),
                             params).scalar_one()
        rows = conn.execute(text(
            "SELECT review_id,source_record_id,review_version,actor,status,review_json,"
            "content_sha256,created_at FROM job_catalog_review" + where +
            " ORDER BY created_at DESC,review_id DESC LIMIT :limit OFFSET :offset"),
            params).mappings().all()
        items = [_summary(row, _checked(row)) for row in rows]
    return {"total": total, "limit": limit, "offset": offset,
            "has_more": offset + len(items) < total, "items": items}


def read_admin_job_review(mysql, review_id):
    if not isinstance(review_id, str) or not 1 <= len(review_id) <= 64:
        raise ValueError("review_id 无效")
    with mysql.engine.begin() as conn:
        row = conn.execute(text(
            "SELECT review_id,source_record_id,review_version,actor,status,review_json,"
            "content_sha256,created_at FROM job_catalog_review WHERE review_id=:id"),
            {"id": review_id}).mappings().first()
        if row is None:
            return None
        review = _checked(row)
        summary = _summary(row, review)
    candidate = review.get("candidate_snapshot")
    candidate_for_preview = None
    if summary["status"] == "candidate_ready":
        if not isinstance(candidate, dict):
            raise JobReviewCorrupt("模型候选缺失")
        try:
            validate_snapshot(candidate)
            if scoring_readiness(candidate):
                raise JobReviewCorrupt("模型候选不满足当前评分字段门禁")
        except (ValueError, TypeError, KeyError) as exc:
            raise JobReviewCorrupt("模型候选无法通过当前快照校验") from exc
        candidate_for_preview = {
            key: value for key, value in candidate.items()
            if key not in _MODEL_ONLY_CANDIDATE_FIELDS
        }
    return {**summary, "review": review,
            "candidate_for_preview": candidate_for_preview}
