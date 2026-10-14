"""Bounded, read-only view of the operator-published job catalog.

Publication is not vacancy freshness: a published snapshot can outlive the
underlying advertisement. Consumers must show ``vacancy_validity=unknown``
unless a separately verified live-source check exists.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from .job_publication import read_published_job


_MAX_PAGE_SIZE = 50


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()
    return str(value)


def _unavailable(reason):
    return {"status": "unavailable", "reason": reason, "total": 0, "items": [],
            "catalog_version": None, "catalog_latest_published_at": None,
            "has_more": False, "limit": 0, "offset": 0}


def list_published_job_candidates(mysql, *, limit=20, offset=0, now=None):
    """Return latest non-revoked publications; never upgrade review rows.

    ``catalog_version`` is an invalidation marker, not an authorization token.
    Each read queries the database anew. A later publication/revocation changes
    the marker; consumers must still verify per-item content hashes.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_PAGE_SIZE:
        raise ValueError("limit 必须在 1..50")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset 必须是非负整数")
    try:
        state = mysql.fetch_all(
            "SELECT COUNT(*) AS row_count, COALESCE(MAX(id), 0) AS max_id, "
            "COALESCE(SUM(CASE WHEN status='published' THEN id ELSE 0 END), 0) AS published_id_sum, "
            "COALESCE(SUM(CASE WHEN status='revoked' THEN id ELSE 0 END), 0) AS revoked_id_sum "
            "FROM job_catalog_publication")[0]
        summary = mysql.fetch_all(
            "SELECT COUNT(*) AS n, MAX(p.published_at) AS latest_published_at "
            "FROM job_catalog_publication p "
            "JOIN (SELECT job_portrait_id, MAX(id) AS latest_id FROM job_catalog_publication "
            "GROUP BY job_portrait_id) latest ON latest.latest_id=p.id "
            "WHERE p.status='published'")[0]
        count = summary["n"]
        rows = mysql.fetch_all(
            "SELECT p.id, p.job_portrait_id, p.publication_version, p.published_at "
            "FROM job_catalog_publication p "
            "JOIN (SELECT job_portrait_id, MAX(id) AS latest_id FROM job_catalog_publication "
            "GROUP BY job_portrait_id) latest ON latest.latest_id=p.id "
            "WHERE p.status='published' ORDER BY p.id DESC LIMIT :limit OFFSET :offset",
            {"limit": limit, "offset": offset})
    except SQLAlchemyError:
        return _unavailable("catalog_database_unavailable")

    # A marker only: not a snapshot transaction or a replacement for current
    # publication and content-hash verification at use time.
    marker = ":".join(str(state[key]) for key in
                      ("row_count", "max_id", "published_id_sum", "revoked_id_sum"))
    version = "jobpub-" + hashlib.sha256(marker.encode("ascii")).hexdigest()[:24]
    items = []
    try:
        for row in rows:
            publication = read_published_job(mysql, row["job_portrait_id"], row["publication_version"])
            latest = read_published_job(mysql, row["job_portrait_id"])
            if (publication is None or latest is None or publication["id"] != row["id"]
                    or latest["id"] != row["id"]):
                return _unavailable("catalog_changed_during_read")
            snapshot = publication["snapshot_json"]
            items.append({
                "job_portrait_id": row["job_portrait_id"],
                "publication_version": row["publication_version"],
                "content_sha256": publication["content_sha256"],
                "published_at": _iso(row["published_at"]),
                "position_name": snapshot["position_name"],
                "source_uri": snapshot["source_uri"],
                "source_fetched_at": snapshot["source_fetched_at"],
                "reviewed_at": snapshot["reviewed_at"],
                "source_license": snapshot["source_license"],
                "publication_status": "published",
                "vacancy_validity": "unknown",
            })
        # The catalog is intentionally read without holding a transaction
        # across page assembly. Recheck its marker so a concurrent publication
        # or revocation of another job cannot make this page look current under
        # the marker captured above.
        final_state = mysql.fetch_all(
            "SELECT COUNT(*) AS row_count, COALESCE(MAX(id), 0) AS max_id, "
            "COALESCE(SUM(CASE WHEN status='published' THEN id ELSE 0 END), 0) AS published_id_sum, "
            "COALESCE(SUM(CASE WHEN status='revoked' THEN id ELSE 0 END), 0) AS revoked_id_sum "
            "FROM job_catalog_publication")[0]
        final_marker = ":".join(str(final_state[key]) for key in
                                ("row_count", "max_id", "published_id_sum", "revoked_id_sum"))
        if final_marker != marker:
            return _unavailable("catalog_changed_during_read")
    except (ValueError, KeyError, SQLAlchemyError):
        return _unavailable("catalog_integrity_unavailable")

    return {"status": "ready" if count else "empty", "reason": None,
            "total": int(count), "items": items, "catalog_version": version,
            "catalog_latest_published_at": _iso(summary["latest_published_at"]),
            "has_more": offset + len(items) < count, "limit": limit, "offset": offset}
