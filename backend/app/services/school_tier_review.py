"""Human-approved school tier from a fetched, exact-host official source.

The confirmed resume remains immutable. This repository supplies a separate,
versioned scoring input only while its approval is current and not revoked.
"""

import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit

import urllib3
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from urllib3.util import Timeout

from .job_publication import canonical_json
from .pipeline_events import ConfirmationTransaction


APPROVABLE_TIERS = frozenset({"985", "211", "双一流"})
OFFICIAL_HOSTS = frozenset({"www.moe.gov.cn", "moe.gov.cn"})
# A school site is authorized only for that school's exact L0 original name.
# This is deliberately not an ``*.edu.cn`` wildcard.
SCHOOL_OFFICIAL_HOSTS = {"清华大学": frozenset({"www.tsinghua.edu.cn"})}
MAX_SOURCE_BYTES = 1_500_000
MAX_QUOTE_CHARS = 800


class SchoolTierReviewConflict(ValueError):
    pass


class SchoolTierSourceUnavailable(RuntimeError):
    pass


class SchoolTierReviewForbidden(PermissionError):
    pass


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def _normal_text(value):
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _checked_uri(raw, *, school_name, source_kind):
    if (not isinstance(raw, str) or not raw or len(raw) > 1000
            or any(ord(char) < 32 or ord(char) == 127 for char in raw)):
        raise ValueError("官方来源 URI 无效")
    uri = urlsplit(raw)
    hosts = (OFFICIAL_HOSTS if source_kind == "education_authority" else
             SCHOOL_OFFICIAL_HOSTS.get(school_name, frozenset()) if source_kind == "school_official" else
             frozenset())
    if (uri.scheme != "https" or uri.hostname not in hosts
            or uri.username is not None or uri.password is not None
            or uri.port not in (None, 443) or uri.fragment):
        raise ValueError("首期只接受已登记学校或教育部的官方 HTTPS 原文页面")
    return uri


def fetch_official_source(uri, *, school_name, source_kind):
    """Resolve once, reject non-public IPs, and pin TLS/SNI to that decision."""
    parsed = _checked_uri(uri, school_name=school_name, source_kind=source_kind)
    host = parsed.hostname
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = sorted({item[4][0] for item in answers})
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise SchoolTierSourceUnavailable("官方来源地址不是公网地址")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        pool = urllib3.HTTPSConnectionPool(
            ips[0], port=443, assert_hostname=host, server_hostname=host,
            cert_reqs=ssl.CERT_REQUIRED, timeout=Timeout(connect=3, read=8), retries=False,
        )
        try:
            response = pool.urlopen(
                "GET", path, headers={"Host": host, "Accept": "text/html,text/plain",
                                      "Accept-Encoding": "identity", "User-Agent": "CPA-Agent/1.0"},
                redirect=False, preload_content=False, decode_content=False,
            )
            try:
                if response.status != 200:
                    raise SchoolTierSourceUnavailable("官方来源当前不可读取")
                content_type = response.headers.get("Content-Type", "").lower()
                if not (content_type.startswith("text/html") or content_type.startswith("text/plain")):
                    raise SchoolTierSourceUnavailable("首期只接受可核对的官方文本页面")
                body = response.read(MAX_SOURCE_BYTES + 1)
            finally:
                response.close()
        finally:
            pool.close()
        if not body or len(body) > MAX_SOURCE_BYTES:
            raise SchoolTierSourceUnavailable("官方来源内容为空或超出大小限制")
        charset = re.search(r"charset=([A-Za-z0-9_-]+)", content_type)
        encodings = [charset.group(1)] if charset else []
        encodings += ["utf-8", "gb18030"]
        decoded = None
        for encoding in encodings:
            try:
                decoded = body.decode(encoding)
                break
            except (UnicodeError, LookupError):
                continue
        if decoded is None:
            raise SchoolTierSourceUnavailable("官方来源编码无法核对")
        if content_type.startswith("text/html"):
            parser = _VisibleText()
            parser.feed(decoded)
            visible = _normal_text(" ".join(parser.parts))
        else:
            visible = _normal_text(decoded)
        return {"source_sha256": hashlib.sha256(body).hexdigest(), "visible_text": visible,
                "source_checked_at": datetime.now(timezone.utc).replace(tzinfo=None)}
    except (OSError, urllib3.exceptions.HTTPError) as exc:
        raise SchoolTierSourceUnavailable("官方来源当前不可核验") from exc


def _evidence_fields(row):
    return {key: row[key] for key in (
        "student_id", "confirmation_task_id", "school_name", "school_tier", "source_kind",
        "source_uri", "source_sha256", "source_quote", "quote_sha256", "reviewed_by")}


def _evidence_digest(row):
    return hashlib.sha256(canonical_json(_evidence_fields(row)).encode("utf-8")).hexdigest()


def _checked_review(row):
    if not row:
        return None
    row = dict(row)
    if (row["source_kind"] not in {"education_authority", "school_official"}
            or row["school_tier"] not in APPROVABLE_TIERS
            or row["quote_sha256"] != hashlib.sha256(row["source_quote"].encode("utf-8")).hexdigest()
            or row["evidence_sha256"] != _evidence_digest(row)):
        raise SchoolTierSourceUnavailable("学校档位审核记录完整性错误")
    try:
        _checked_uri(row["source_uri"], school_name=row["school_name"], source_kind=row["source_kind"])
    except ValueError as exc:
        raise SchoolTierSourceUnavailable("学校档位审核来源失效") from exc
    return row


def _missing_review_table(exc):
    return getattr(getattr(exc, "orig", None), "args", (None,))[0] == 1146


def _review_schema_not_yet_migrated(mysql):
    try:
        versions = mysql.fetch_all("SELECT version_num FROM alembic_version")
    except ProgrammingError as exc:
        if _missing_review_table(exc):
            return True
        raise
    # A recorded 0005 (or a later version) promises this table. Never turn a
    # dropped/corrupt approval ledger into apparent absence of approvals.
    return not versions or all(str(row["version_num"]).startswith((
        "0001_", "0002_", "0003_", "0004_")) for row in versions)


def latest_review(mysql, *, student_id, confirmation_task_id, lock=False,
                  allow_unmigrated=False):
    suffix = " FOR UPDATE" if lock else ""
    try:
        rows = mysql.fetch_all(
            "SELECT id,student_id,confirmation_task_id,school_name,school_tier,source_kind,"
            "source_uri,source_sha256,source_quote,quote_sha256,evidence_sha256,"
            "source_checked_at,status,reviewed_by,reviewed_at,revoked_by,revoked_at,revoke_reason "
            "FROM school_tier_review WHERE student_id=:sid AND confirmation_task_id=:tid "
            "ORDER BY id DESC LIMIT 1" + suffix,
            {"sid": student_id, "tid": confirmation_task_id},
        )
    except ProgrammingError as exc:
        if not _missing_review_table(exc):
            raise
        if allow_unmigrated and _review_schema_not_yet_migrated(mysql):
            return None
        raise SchoolTierSourceUnavailable("学校档位审核结构缺失") from exc
    return _checked_review(rows[0]) if rows else None


def approval_for_l2(mysql, *, student_id, confirmation_task_id, school_name, lock=False):
    row = latest_review(mysql, student_id=student_id, confirmation_task_id=confirmation_task_id,
                        lock=lock)
    if row and row["status"] == "approved" and row["school_name"] == school_name:
        return row
    return None


def review_reference(row):
    return {"id": row["id"], "evidence_sha256": row["evidence_sha256"]} if row else None


def portrait_review_current(mysql, *, student_id, confirmation_task_id, portrait):
    """Revocation, supersession or new approval invalidates an older L2 score."""
    latest = latest_review(mysql, student_id=student_id,
                           confirmation_task_id=confirmation_task_id,
                           allow_unmigrated=True)
    reference = portrait.get("school_tier_review") if isinstance(portrait, dict) else None
    if latest is None:
        return reference is None
    return (latest["status"] == "approved" and isinstance(reference, dict)
            and reference == review_reference(latest)
            and portrait.get("D1_basic", {}).get("school_tier") == latest["school_tier"]
            and portrait.get("D1_basic", {}).get("school_score_basis") == "reviewed_official_school_tier")


def _current_admin(conn, actor_id):
    row = conn.execute(text("SELECT role,status FROM user_account WHERE id=:id FOR UPDATE"),
                       {"id": actor_id}).mappings().first()
    if not row or row["role"] != "admin" or row["status"] != "active":
        raise SchoolTierReviewForbidden("管理员账号不再有效")


def _current_profile(conn, *, student_id, confirmation_task_id, school_name):
    from .pipeline import require_confirmed_profile
    conn.execute(text("SELECT student_id FROM student_profile WHERE student_id=:sid FOR UPDATE"),
                 {"sid": student_id}).first()
    profile = require_confirmed_profile(ConfirmationTransaction(conn), student_id)
    if (profile["_resume_confirmation"]["task_id"] != confirmation_task_id
            or profile.get("school") != school_name):
        raise SchoolTierReviewConflict("已确认简历版本或学校名称发生变化")
    return profile


def _audit(conn, *, actor_id, action, review_id, digest, ip, reason=None):
    detail = {"review_id": review_id, "evidence_sha256": digest}
    if reason:
        detail["reason"] = reason
    conn.execute(text(
        "INSERT INTO admin_audit_log (admin_id,action,target_type,target_id,detail_json,ip,created_at) "
        "VALUES (:actor,:action,'school_tier_review',:id,:detail,:ip,NOW())"),
        {"actor": actor_id, "action": action, "id": str(review_id),
         "detail": canonical_json(detail), "ip": ip})


def approve_school_tier(mysql, *, body, actor_id, ip=None):
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON 对象")
    required = {"student_id", "confirmation_task_id", "school_name", "school_tier",
                "source_kind", "source_uri", "source_quote", "confirm_official_evidence"}
    if set(body) != required or body.get("confirm_official_evidence") is not True:
        raise ValueError("需完整填写并明确确认官方来源证据")
    for field, maximum in (("student_id", 64), ("confirmation_task_id", 64),
                           ("school_name", 255), ("source_uri", 1000),
                           ("source_quote", MAX_QUOTE_CHARS)):
        value = body[field]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} 无效")
    if body["school_tier"] not in APPROVABLE_TIERS:
        raise ValueError("首期仅支持官方原文明确的 985、211、双一流档位")
    if body["source_kind"] not in {"education_authority", "school_official"}:
        raise ValueError("首期仅支持教育部或已登记学校官方原文")
    school = body["school_name"].strip()
    _checked_uri(body["source_uri"], school_name=school, source_kind=body["source_kind"])
    quote = _normal_text(body["source_quote"])
    tier = body["school_tier"]
    if len(quote) < 8 or school not in quote or tier not in quote:
        raise ValueError("同一官方原文摘录须明确关联学校原名和档位")
    fetched = fetch_official_source(body["source_uri"], school_name=school,
                                    source_kind=body["source_kind"])
    if quote not in fetched["visible_text"]:
        raise SchoolTierReviewConflict("官方页面当前不包含该连续原文摘录")
    record = {"student_id": body["student_id"], "confirmation_task_id": body["confirmation_task_id"],
              "school_name": school, "school_tier": tier, "source_kind": body["source_kind"],
              "source_uri": body["source_uri"], "source_sha256": fetched["source_sha256"],
              "source_quote": quote, "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
              "reviewed_by": actor_id}
    record["evidence_sha256"] = _evidence_digest(record)
    with mysql.engine.begin() as conn:
        _current_admin(conn, actor_id)
        _current_profile(conn, student_id=record["student_id"],
                         confirmation_task_id=record["confirmation_task_id"], school_name=school)
        current = latest_review(ConfirmationTransaction(conn), student_id=record["student_id"],
                                confirmation_task_id=record["confirmation_task_id"], lock=True)
        if current and current["status"] == "approved":
            if current["evidence_sha256"] == record["evidence_sha256"]:
                return {"id": current["id"], "status": "approved",
                        "evidence_sha256": current["evidence_sha256"], "idempotent": True}
            conn.execute(text("UPDATE school_tier_review SET status='superseded',revoked_by=:actor,"
                              "revoked_at=NOW(6),revoke_reason='superseded_by_new_approval' "
                              "WHERE id=:id AND status='approved'"),
                         {"actor": actor_id, "id": current["id"]})
        inserted = conn.execute(text(
            "INSERT INTO school_tier_review (student_id,confirmation_task_id,school_name,school_tier,"
            "source_kind,source_uri,source_sha256,source_quote,quote_sha256,evidence_sha256,"
            "source_checked_at,status,reviewed_by,reviewed_at) "
            "VALUES (:student_id,:confirmation_task_id,:school_name,:school_tier,:source_kind,"
            ":source_uri,:source_sha256,:source_quote,:quote_sha256,:evidence_sha256,"
            ":source_checked_at,'approved',:reviewed_by,NOW(6))"),
            {**record, "source_checked_at": fetched["source_checked_at"]})
        review_id = inserted.lastrowid
        saved = latest_review(ConfirmationTransaction(conn), student_id=record["student_id"],
                              confirmation_task_id=record["confirmation_task_id"])
        if not saved or saved["id"] != review_id or saved["status"] != "approved":
            raise RuntimeError("学校档位审核写入未通过回读")
        _audit(conn, actor_id=actor_id, action="approve_school_tier", review_id=review_id,
               digest=record["evidence_sha256"], ip=ip)
    return {"id": review_id, "status": "approved", "school_tier": tier,
            "evidence_sha256": record["evidence_sha256"], "idempotent": False}


def revoke_school_tier(mysql, *, review_id, expected_digest, reason, actor_id, ip=None):
    if not isinstance(review_id, int) or review_id < 1:
        raise ValueError("审核版本 ID 无效")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ValueError("必须提供审核版本摘要")
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        raise ValueError("撤回理由应为 5–500 字")
    with mysql.engine.begin() as conn:
        row = conn.execute(text(
            "SELECT student_id,confirmation_task_id,school_name FROM school_tier_review WHERE id=:id"),
            {"id": review_id}).mappings().first()
        if not row:
            return None
        _current_admin(conn, actor_id)
        # Same lock order as L2 and approval: owner profile, then review.
        conn.execute(text("SELECT student_id FROM student_profile WHERE student_id=:sid FOR UPDATE"),
                     {"sid": row["student_id"]}).first()
        current = latest_review(ConfirmationTransaction(conn), student_id=row["student_id"],
                                confirmation_task_id=row["confirmation_task_id"], lock=True)
        if (not current or current["id"] != review_id or current["status"] != "approved"
                or current["evidence_sha256"] != expected_digest):
            raise SchoolTierReviewConflict("审核版本已变化或已撤回")
        updated = conn.execute(text(
            "UPDATE school_tier_review SET status='revoked',revoked_by=:actor,"
            "revoked_at=NOW(6),revoke_reason=:reason WHERE id=:id AND status='approved'"),
            {"actor": actor_id, "reason": reason.strip(), "id": review_id})
        if updated.rowcount != 1:
            raise SchoolTierReviewConflict("审核版本已变化")
        _audit(conn, actor_id=actor_id, action="revoke_school_tier", review_id=review_id,
               digest=expected_digest, ip=ip, reason=reason.strip())
    return {"id": review_id, "status": "revoked", "evidence_sha256": expected_digest}
