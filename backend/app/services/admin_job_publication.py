"""DB-transactional admin publication and revocation of reviewed job snapshots.

This module does not fetch source URLs or turn model review into human approval.
The authenticated admin attests to a separately reviewed, exact candidate digest.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy import text

from .job_publication import (canonical_json, content_sha256,
                              scoring_readiness, validate_snapshot)

_CANDIDATE_FIELDS = frozenset({
    "position_name", "source_uri", "source_text", "source_text_sha256",
    "source_fetched_at", "source_closes_at", "source_license", "source_license_uri",
    "source_record_id", "source_record_sha256", "source_feed_sha256",
    "field_evidence", "required_skills", "job_profile",
})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PublicationNotReady(ValueError):
    def __init__(self, missing):
        super().__init__("岗位来源字段不足以进入现有评分器")
        self.missing = missing


class PublicationConflict(ValueError):
    pass


class PublicationForbidden(ValueError):
    pass


def _ident(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError(f"{name} 必须是 1 至 64 字符的文本")
    return value.strip()


def prepare_candidate(*, job_portrait_id, publication_version, candidate, actor_id):
    """Validate the untrusted exact candidate; never accept browser review metadata."""
    job_portrait_id = _ident(job_portrait_id, "job_portrait_id")
    if job_portrait_id.startswith("source_csb_"):
        raise ValueError("source_csb_ 是仅供来源展示的保留岗位 ID 前缀")
    publication_version = _ident(publication_version, "publication_version")
    _ident(actor_id, "reviewer")
    if not isinstance(candidate, dict) or set(candidate) - _CANDIDATE_FIELDS:
        raise ValueError("snapshot 含未知字段或客户端审核元数据")
    if not isinstance(candidate.get("source_text"), str) or len(candidate["source_text"]) > 200_000:
        raise ValueError("source_text 无效或超出 200000 字符")
    if (not isinstance(candidate.get("source_uri"), str)
            or len(candidate["source_uri"]) > 2048
            or not isinstance(candidate.get("source_license"), str)
            or len(candidate["source_license"]) > 1000):
        raise ValueError("来源 URI 或许可声明无效或过长")
    now = datetime.now(timezone.utc)
    closes = candidate.get("source_closes_at")
    if closes is not None:
        if not isinstance(closes, str):
            raise ValueError("source_closes_at 必须是带时区时间")
        try:
            parsed = datetime.fromisoformat(closes.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("source_closes_at 时间无效") from exc
        if parsed.tzinfo is None or parsed <= now:
            raise ValueError("岗位来源截止时间已到或缺少时区")
    # Existing validator needs review metadata. Insert only server-owned, pending
    # values here; the committed approved timestamp is generated later.
    preview_snapshot = {**candidate, "reviewed_by": actor_id,
                        "reviewed_at": now.isoformat(), "review_state": "pending"}
    validate_snapshot(preview_snapshot)
    missing = scoring_readiness(preview_snapshot)
    if missing:
        raise PublicationNotReady(missing)
    digest = content_sha256(candidate)
    return {"job_portrait_id": job_portrait_id,
            "publication_version": publication_version,
            "candidate_sha256": digest,
            "source_text_sha256": candidate["source_text_sha256"],
            "preview_snapshot": preview_snapshot}


def _require_current_admin(conn, actor_id):
    row = conn.execute(text("SELECT role,status FROM user_account WHERE id=:id FOR UPDATE"),
                       {"id": actor_id}).mappings().first()
    if not row or row["role"] != "admin" or row["status"] != "active":
        raise PublicationForbidden("管理员账号不再有效")


def _audit(conn, *, actor_id, action, job_id, publication_id, version,
           content_digest, source_digest, candidate_digest=None, reason=None, ip=None):
    detail = {"publication_id": publication_id, "publication_version": version,
              "content_sha256": content_digest, "source_text_sha256": source_digest}
    if candidate_digest is not None:
        detail["candidate_sha256"] = candidate_digest
    if reason is not None:
        detail["reason"] = reason
    conn.execute(text(
        "INSERT INTO admin_audit_log (admin_id,action,target_type,target_id,detail_json,ip,created_at) "
        "VALUES (:actor,:action,'job_catalog_publication',:jid,:detail,:ip,NOW())"),
        {"actor": actor_id, "action": action, "jid": job_id,
         "detail": canonical_json(detail), "ip": ip})


def publish_reviewed_candidate(mysql, *, prepared, candidate, actor_id, ip=None):
    """Atomic immutable version + audit; no UPSERT or user-controlled reviewer."""
    if prepared["candidate_sha256"] != content_sha256(candidate):
        raise PublicationConflict("岗位候选在预检后已改变")
    snapshot = {**candidate, "reviewed_by": actor_id,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "review_state": "approved"}
    validate_snapshot(snapshot)
    missing = scoring_readiness(snapshot)
    if missing:
        raise PublicationNotReady(missing)
    payload = canonical_json(snapshot)
    digest = content_sha256(snapshot)
    with mysql.engine.begin() as conn:
        _require_current_admin(conn, actor_id)
        existing = conn.execute(text(
            "SELECT id FROM job_catalog_publication WHERE job_portrait_id=:jid "
            "AND publication_version=:version FOR UPDATE"),
            {"jid": prepared["job_portrait_id"],
             "version": prepared["publication_version"]}).first()
        if existing:
            raise PublicationConflict("该岗位发布版本已存在；不得覆盖或伪装重试成功")
        inserted = conn.execute(text(
            "INSERT INTO job_catalog_publication (job_portrait_id, publication_version, "
            "snapshot_json, content_sha256, status, published_at) "
            "VALUES (:jid,:version,:payload,:digest,'published',NOW())"),
            {"jid": prepared["job_portrait_id"],
             "version": prepared["publication_version"],
             "payload": payload, "digest": digest})
        publication_id = inserted.lastrowid
        row = conn.execute(text(
            "SELECT snapshot_json,content_sha256,status FROM job_catalog_publication "
            "WHERE id=:id"), {"id": publication_id}).mappings().one()
        saved = row["snapshot_json"]
        saved = json.loads(saved) if isinstance(saved, str) else saved
        if (row["status"] != "published" or row["content_sha256"] != digest
                or saved != snapshot):
            raise RuntimeError("岗位发布事务内回读失败")
        _audit(conn, actor_id=actor_id, action="publish",
               job_id=prepared["job_portrait_id"], publication_id=publication_id,
               version=prepared["publication_version"], content_digest=digest,
               source_digest=candidate["source_text_sha256"],
               candidate_digest=prepared["candidate_sha256"], ip=ip)
    return {"id": publication_id, "job_portrait_id": prepared["job_portrait_id"],
            "publication_version": prepared["publication_version"],
            "content_sha256": digest, "source_text_sha256": candidate["source_text_sha256"],
            "reviewed_by": actor_id, "reviewed_at": snapshot["reviewed_at"],
            "status": "published"}


def revoke_publication(mysql, *, publication_id, expected_digest, reason, actor_id, ip=None):
    if type(publication_id) is not int or publication_id <= 0:
        raise ValueError("publication_id 无效")
    if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
        raise ValueError("expected_content_sha256 无效")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise ValueError("reason 必须是 1 至 500 字符的说明")
    with mysql.engine.begin() as conn:
        _require_current_admin(conn, actor_id)
        row = conn.execute(text(
            "SELECT id,job_portrait_id,publication_version,snapshot_json,content_sha256,status "
            "FROM job_catalog_publication WHERE id=:id FOR UPDATE"),
            {"id": publication_id}).mappings().first()
        if row is None:
            return None
        if row["status"] != "published":
            raise PublicationConflict("该发布版本已撤回")
        if row["content_sha256"] != expected_digest:
            raise PublicationConflict("发布内容摘要与预期不一致")
        snapshot = row["snapshot_json"]
        snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        if content_sha256(snapshot) != expected_digest:
            raise PublicationConflict("已存发布快照摘要不一致")
        conn.execute(text("UPDATE job_catalog_publication SET status='revoked' "
                          "WHERE id=:id AND status='published'"), {"id": publication_id})
        _audit(conn, actor_id=actor_id, action="revoke",
               job_id=row["job_portrait_id"], publication_id=publication_id,
               version=row["publication_version"], content_digest=expected_digest,
               source_digest=snapshot["source_text_sha256"],
               reason=reason.strip(), ip=ip)
    return {"id": publication_id, "job_portrait_id": row["job_portrait_id"],
            "publication_version": row["publication_version"],
            "content_sha256": expected_digest, "status": "revoked"}


def read_admin_publication(mysql, publication_id):
    """Read any version, including revoked, without treating it as public."""
    if type(publication_id) is not int or publication_id <= 0:
        raise ValueError("publication_id 无效")
    row = mysql.execute_single(
        "SELECT id,job_portrait_id,publication_version,snapshot_json,content_sha256,"
        "status,published_at FROM job_catalog_publication WHERE id=:id",
        {"id": publication_id})
    if row is None:
        return None
    snapshot = row["snapshot_json"]
    snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
    validate_snapshot(snapshot)
    if content_sha256(snapshot) != row["content_sha256"]:
        raise PublicationConflict("岗位发布内容摘要不一致")
    return {"id": row["id"], "job_portrait_id": row["job_portrait_id"],
            "publication_version": row["publication_version"],
            "content_sha256": row["content_sha256"], "status": row["status"],
            "published_at": row["published_at"].isoformat(), "snapshot": snapshot}


def list_admin_publications(mysql, *, limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 无效")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 无效")
    total = mysql.execute_single("SELECT COUNT(*) AS n FROM job_catalog_publication")["n"]
    rows = mysql.execute(
        "SELECT id FROM job_catalog_publication ORDER BY id DESC LIMIT :limit OFFSET :offset",
        {"limit": limit, "offset": offset})
    items = []
    for row in rows:
        detail = read_admin_publication(mysql, row["id"])
        if detail is None:
            raise PublicationConflict("分页期间发布版本已改变，请重试")
        snapshot = detail.pop("snapshot")
        detail.update({"position_name": snapshot["position_name"],
                       "source_uri": snapshot["source_uri"],
                       "source_fetched_at": snapshot["source_fetched_at"],
                       "reviewed_by": snapshot["reviewed_by"],
                       "reviewed_at": snapshot["reviewed_at"]})
        items.append(detail)
    return {"total": total, "limit": limit, "offset": offset,
            "has_more": offset + len(items) < total, "items": items}
