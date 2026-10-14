"""Official-source informational jobs, strictly separate from scored publications.

Only a fixed CSB feed and exact, digest-checked model audit may enter this
repository. A human account approves *display of the original source*, never
the model's unrepresentable requirements or any L3/L4 scoring eligibility.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from .admin_job_review import _checked as checked_model_review
from .job_catalog_review import FEED_URI, LICENSE_URI
from .job_publication import canonical_json, content_sha256

CATALOG_PREFIX = "source_csb_"
_RECORD_ID = re.compile(r"[0-9]{1,50}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class SourceListingConflict(ValueError):
    pass


class SourceListingUnavailable(RuntimeError):
    pass


class SourceListingForbidden(ValueError):
    pass


def is_source_only_id(job_id):
    return isinstance(job_id, str) and job_id.startswith(CATALOG_PREFIX)


def catalog_id(record_id):
    if not isinstance(record_id, str) or not _RECORD_ID.fullmatch(record_id):
        raise ValueError("官方来源记录 ID 无效")
    return CATALOG_PREFIX + record_id


def _parse_closes(record, now):
    raw = record.get("enddate") if isinstance(record, dict) else None
    if not isinstance(raw, str):
        raise SourceListingConflict("官方来源截止日期缺失")
    try:
        closes = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceListingConflict("官方来源截止日期无效") from exc
    if closes.tzinfo is None:
        closes = closes.replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
    if closes <= now:
        raise SourceListingConflict("官方来源记录已过截止日期")
    return closes.isoformat()


def _review_source(review, *, expected_review_sha256, expected_record_sha256):
    if review is None:
        raise LookupError("模型岗位审查不存在")
    if review["content_sha256"] != expected_review_sha256:
        raise SourceListingConflict("模型审查摘要与预期不一致")
    payload = review["review"]
    record = payload.get("source_record")
    record_id = payload.get("source_record_id")
    if (payload.get("source_uri") != FEED_URI or not isinstance(record, dict)
            or not isinstance(record_id, str) or not _RECORD_ID.fullmatch(record_id)
            or str(record.get("jobid")) != record_id
            or not isinstance(record.get("jobname"), str) or not record["jobname"].strip()
            or not isinstance(record.get("entreq"), str) or not record["entreq"].strip()
            or payload.get("source_record_sha256") != expected_record_sha256
            or content_sha256(record) != expected_record_sha256):
        raise SourceListingConflict("模型审查不含可核对的官方原始记录")
    return record_id, record


def _current_admin(conn, actor_id):
    row = conn.execute(text("SELECT role,status FROM user_account WHERE id=:id FOR UPDATE"),
                       {"id": actor_id}).mappings().first()
    if not row or row["role"] != "admin" or row["status"] != "active":
        raise SourceListingForbidden("管理员账号不再有效")


def _audit(conn, *, actor_id, action, listing_id, snapshot, reason=None, ip=None):
    detail = {"listing_id": listing_id, "source_record_id": snapshot["source_record_id"],
              "listing_version": snapshot["listing_version"],
              "content_sha256": content_sha256(snapshot),
              "source_record_sha256": snapshot["source_record_sha256"],
              "review_id": snapshot["review_id"]}
    if reason:
        detail["reason"] = reason
    conn.execute(text(
        "INSERT INTO admin_audit_log (admin_id,action,target_type,target_id,detail_json,ip,created_at) "
        "VALUES (:actor,:action,'job_catalog_source_listing',:id,:detail,:ip,NOW())"),
        {"actor": actor_id, "action": action, "id": str(listing_id),
         "detail": canonical_json(detail), "ip": ip})


def list_official_source_only(mysql, *, review_id, listing_version,
                              expected_review_sha256, expected_record_sha256,
                              actor_id, ip=None):
    """Re-fetch exact official record, then atomically list source-only + audit."""
    from .admin_job_review import read_admin_job_review
    from . import job_catalog_review

    if not isinstance(review_id, str) or not 1 <= len(review_id) <= 64:
        raise ValueError("review_id 无效")
    if (not isinstance(listing_version, str) or not listing_version.strip()
            or len(listing_version) > 64):
        raise ValueError("listing_version 无效")
    if (not isinstance(expected_review_sha256, str)
            or not _SHA256.fullmatch(expected_review_sha256)
            or not isinstance(expected_record_sha256, str)
            or not _SHA256.fullmatch(expected_record_sha256)):
        raise ValueError("来源摘要无效")
    review = read_admin_job_review(mysql, review_id)
    record_id, _ = _review_source(review,
        expected_review_sha256=expected_review_sha256,
        expected_record_sha256=expected_record_sha256)
    try:
        records, feed_digest, checked_at = job_catalog_review.fetch_official_feed()
    except Exception as exc:
        raise SourceListingUnavailable("官方来源暂不可核验") from exc
    matches = [record for record in records if isinstance(record, dict)
               and str(record.get("jobid")) == record_id]
    if len(matches) != 1:
        raise SourceListingConflict("官方来源记录不存在或不唯一")
    current_record = matches[0]
    if content_sha256(current_record) != expected_record_sha256:
        raise SourceListingConflict("官方来源记录已变化，请重新审查")
    now = datetime.now(timezone.utc)
    closes_at = _parse_closes(current_record, now)
    try:
        checked_time = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SourceListingUnavailable("官方来源核验时间无效") from exc
    if checked_time.tzinfo is None or checked_time > now or (now - checked_time).total_seconds() > 120:
        raise SourceListingUnavailable("官方来源核验时间不可信")
    if not isinstance(feed_digest, str) or not _SHA256.fullmatch(feed_digest):
        raise SourceListingUnavailable("官方来源摘要无效")

    snapshot = {"listing_kind": "source_only", "listing_version": listing_version.strip(),
                "source_name": "hk_csb", "source_uri": FEED_URI,
                "source_record_id": record_id, "source_record": current_record,
                "source_record_sha256": expected_record_sha256,
                "source_feed_sha256": feed_digest,
                "source_checked_at": checked_at, "source_closes_at": closes_at,
                "source_license": "DATA.GOV.HK Terms of Use v1.2; attribute Government/CSB/DATA.GOV.HK",
                "source_license_uri": LICENSE_URI,
                "review_id": review_id, "review_sha256": expected_review_sha256,
                "approved_by": actor_id, "approved_at": now.isoformat(),
                "match_eligible": False, "scoring_status": "not_supported"}
    digest = content_sha256(snapshot)
    with mysql.engine.begin() as conn:
        _current_admin(conn, actor_id)
        locked = conn.execute(text(
            "SELECT review_id,source_record_id,review_version,actor,status,review_json,"
            "content_sha256,created_at FROM job_catalog_review WHERE review_id=:id FOR UPDATE"),
            {"id": review_id}).mappings().first()
        if locked is None:
            raise SourceListingConflict("模型审查已不存在")
        checked_review = checked_model_review(locked)
        if (locked["content_sha256"] != expected_review_sha256
                or checked_review.get("source_record_sha256") != expected_record_sha256):
            raise SourceListingConflict("模型审查在核验期间变化")
        prior = conn.execute(text(
            "SELECT id,status,content_sha256 FROM job_catalog_source_listing "
            "WHERE source_record_id=:rid ORDER BY id DESC LIMIT 1 FOR UPDATE"),
            {"rid": record_id}).mappings().first()
        if prior and prior["status"] == "listed":
            raise SourceListingConflict("该官方来源已有公开版本，须先撤回或待来源变化")
        existing_version = conn.execute(text(
            "SELECT id FROM job_catalog_source_listing WHERE source_record_id=:rid "
            "AND listing_version=:version FOR UPDATE"),
            {"rid": record_id, "version": snapshot["listing_version"]}).first()
        if existing_version:
            raise SourceListingConflict("该来源展示版本已存在")
        inserted = conn.execute(text(
            "INSERT INTO job_catalog_source_listing (source_record_id,listing_version,snapshot_json,"
            "content_sha256,status,listed_at) VALUES (:rid,:version,:payload,:digest,'listed',NOW())"),
            {"rid": record_id, "version": snapshot["listing_version"],
             "payload": canonical_json(snapshot), "digest": digest})
        listing_id = inserted.lastrowid
        saved = conn.execute(text(
            "SELECT snapshot_json,content_sha256,status FROM job_catalog_source_listing WHERE id=:id"),
            {"id": listing_id}).mappings().one()
        saved_snapshot = saved["snapshot_json"]
        saved_snapshot = json.loads(saved_snapshot) if isinstance(saved_snapshot, str) else saved_snapshot
        if saved["status"] != "listed" or saved["content_sha256"] != digest or saved_snapshot != snapshot:
            raise RuntimeError("来源专用岗位事务内回读失败")
        _audit(conn, actor_id=actor_id, action="list_source_only", listing_id=listing_id,
               snapshot=snapshot, ip=ip)
    # A successful approval can prime the shared read cache without making
    # Redis availability part of the publication transaction.  Beat continues
    # to refresh it; a failed prime leaves the source-only item hidden.
    try:
        from .job_source_verification import cache_verified_feed
        cache_verified_feed(records, feed_digest, checked_at)
        verification_cache = "ready"
    except Exception:
        verification_cache = "pending"
    return {"id": listing_id, "catalog_id": catalog_id(record_id),
            "status": "listed", "content_sha256": digest,
            "match_eligible": False, "scoring_status": "not_supported",
            "verification_cache": verification_cache}


def _checked_listing(row):
    try:
        snapshot = row["snapshot_json"]
        snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        if (not isinstance(snapshot, dict) or content_sha256(snapshot) != row["content_sha256"]
                or snapshot.get("source_record_id") != row["source_record_id"]
                or snapshot.get("listing_version") != row["listing_version"]
                or snapshot.get("listing_kind") != "source_only"
                or snapshot.get("source_name") != "hk_csb"
                or snapshot.get("source_uri") != FEED_URI
                or snapshot.get("match_eligible") is not False
                or snapshot.get("scoring_status") != "not_supported"
                or not isinstance(snapshot.get("source_record"), dict)
                or content_sha256(snapshot["source_record"]) != snapshot.get("source_record_sha256")
                or str(snapshot["source_record"].get("jobid")) != row["source_record_id"]):
            raise SourceListingUnavailable("来源专用岗位完整性错误")
        return snapshot
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise SourceListingUnavailable("来源专用岗位记录无法校验") from exc


def _visible(row, snapshot, now):
    try:
        closes = datetime.fromisoformat(snapshot["source_closes_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceListingUnavailable("来源专用岗位截止日期损坏") from exc
    return row["status"] == "listed" and closes.tzinfo is not None and closes > now


def _current_source_records():
    """Use a fresh shared worker verification; never wait on upstream here."""
    from .job_source_verification import read_verified_feed

    verification = read_verified_feed()
    return verification["record_digests"], verification["verified_at"]


def _matches_current_source(record_digests, row, snapshot):
    return record_digests.get(row["source_record_id"]) == snapshot["source_record_sha256"]


def _public_projection(row, snapshot, *, verified_at):
    record_id = snapshot["source_record_id"]
    return {"portrait_id": catalog_id(record_id),
            "job_portrait_id": catalog_id(record_id),
            "source_record_id": record_id,
            "position_name": snapshot["source_record"]["jobname"],
            "source_uri": snapshot["source_uri"],
            "source_fetched_at": snapshot["source_checked_at"],
            "source_verified_at": verified_at,
            "source_closes_at": snapshot["source_closes_at"],
            "source_license": snapshot["source_license"],
            "reviewed_at": snapshot["approved_at"],
            "publication_status": "source_only",
            "vacancy_validity": "unknown",
            "match_eligible": False,
            "scoring_status": "not_supported",
            "content_sha256": row["content_sha256"],
            "listing_version": snapshot["listing_version"]}


def list_source_only_candidates(mysql, *, now=None):
    """Read every latest listed source version; revoked latest never falls back."""
    now = now or datetime.now(timezone.utc)
    rows = mysql.execute(
        "SELECT s.id,s.source_record_id,s.listing_version,s.snapshot_json,s.content_sha256,s.status "
        "FROM job_catalog_source_listing s JOIN "
        "(SELECT source_record_id,MAX(id) AS latest_id FROM job_catalog_source_listing "
        "GROUP BY source_record_id) latest ON latest.latest_id=s.id ORDER BY s.id DESC")
    if not rows:
        return []
    visible = []
    for row in rows:
        snapshot = _checked_listing(row)
        if _visible(row, snapshot, now):
            visible.append((row, snapshot))
    if not visible:
        return []
    current_records, checked_at = _current_source_records()
    items = []
    for row, snapshot in visible:
        if _matches_current_source(current_records, row, snapshot):
            items.append(_public_projection(row, snapshot, verified_at=checked_at))
    return items


def read_source_only_candidate(mysql, job_id, *, now=None):
    if not is_source_only_id(job_id):
        return None
    record_id = job_id[len(CATALOG_PREFIX):]
    if not _RECORD_ID.fullmatch(record_id):
        return None
    row = mysql.execute_single(
        "SELECT id,source_record_id,listing_version,snapshot_json,content_sha256,status "
        "FROM job_catalog_source_listing WHERE source_record_id=:rid "
        "ORDER BY id DESC LIMIT 1", {"rid": record_id})
    if row is None:
        return None
    snapshot = _checked_listing(row)
    if not _visible(row, snapshot, now or datetime.now(timezone.utc)):
        return None
    current_records, checked_at = _current_source_records()
    if not _matches_current_source(current_records, row, snapshot):
        return None
    return {**_public_projection(row, snapshot, verified_at=checked_at),
            "source_record": snapshot["source_record"],
            "portrait_json": None}


def read_admin_source_listing(mysql, listing_id):
    """Read an exact historical version, including revoked records, for audit."""
    if type(listing_id) is not int or listing_id <= 0:
        raise ValueError("来源专用岗位 ID 无效")
    row = mysql.execute_single(
        "SELECT id,source_record_id,listing_version,snapshot_json,content_sha256,status,listed_at "
        "FROM job_catalog_source_listing WHERE id=:id", {"id": listing_id})
    if row is None:
        return None
    snapshot = _checked_listing(row)
    if row["status"] not in {"listed", "revoked"}:
        raise SourceListingUnavailable("来源专用岗位状态无效")
    listed_at = row["listed_at"]
    return {"id": row["id"], "catalog_id": catalog_id(row["source_record_id"]),
            "listing_version": row["listing_version"], "content_sha256": row["content_sha256"],
            "status": row["status"], "listed_at": listed_at.isoformat()
            if isinstance(listed_at, datetime) else str(listed_at), "snapshot": snapshot}


def list_admin_source_listings(mysql, *, limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 无效")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 无效")
    total = mysql.execute_single("SELECT COUNT(*) AS n FROM job_catalog_source_listing")["n"]
    rows = mysql.execute("SELECT id FROM job_catalog_source_listing ORDER BY id DESC "
                         "LIMIT :limit OFFSET :offset", {"limit": limit, "offset": offset})
    items = []
    for row in rows:
        detail = read_admin_source_listing(mysql, row["id"])
        if detail is None:
            raise SourceListingUnavailable("分页期间来源专用岗位记录已变化，请重试")
        snapshot = detail.pop("snapshot")
        detail.update({"position_name": snapshot["source_record"]["jobname"],
                       "source_uri": snapshot["source_uri"],
                       "source_record_sha256": snapshot["source_record_sha256"],
                       "source_checked_at": snapshot["source_checked_at"],
                       "source_closes_at": snapshot["source_closes_at"],
                       "approved_by": snapshot["approved_by"],
                       "approved_at": snapshot["approved_at"],
                       "match_eligible": False, "scoring_status": "not_supported"})
        items.append(detail)
    return {"total": total, "limit": limit, "offset": offset,
            "has_more": offset + len(items) < total, "items": items}


def revoke_source_only(mysql, *, listing_id, expected_digest, reason, actor_id, ip=None):
    if type(listing_id) is not int or listing_id <= 0:
        raise ValueError("来源专用岗位 ID 无效")
    if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
        raise ValueError("expected_content_sha256 无效")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise ValueError("reason 必须是 1 至 500 字符的说明")
    with mysql.engine.begin() as conn:
        _current_admin(conn, actor_id)
        row = conn.execute(text(
            "SELECT id,source_record_id,listing_version,snapshot_json,content_sha256,status "
            "FROM job_catalog_source_listing WHERE id=:id FOR UPDATE"),
            {"id": listing_id}).mappings().first()
        if row is None:
            return None
        if row["status"] != "listed":
            raise SourceListingConflict("来源专用岗位已撤回")
        if row["content_sha256"] != expected_digest:
            raise SourceListingConflict("来源专用岗位摘要与预期不一致")
        snapshot = _checked_listing(row)
        conn.execute(text(
            "UPDATE job_catalog_source_listing SET status='revoked' WHERE id=:id AND status='listed'"),
            {"id": listing_id})
        _audit(conn, actor_id=actor_id, action="revoke_source_only", listing_id=listing_id,
               snapshot=snapshot, reason=reason.strip(), ip=ip)
    return {"id": listing_id, "catalog_id": catalog_id(row["source_record_id"]),
            "status": "revoked", "content_sha256": expected_digest,
            "match_eligible": False}
