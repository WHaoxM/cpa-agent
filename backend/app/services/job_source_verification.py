"""Short-lived, shared verification of the fixed official vacancy feed.

Public requests never fetch the upstream feed.  A Celery Beat task refreshes
this Redis record; absence, corruption or expiry hides source-only listings.
The publication database remains the authority for what an admin approved.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .job_catalog_review import FEED_URI
from .job_publication import canonical_json, content_sha256

_KEY = "cpa:catalog:source:csb:verification:v1"
_MAX_AGE_SECONDS = 300
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class SourceVerificationUnavailable(RuntimeError):
    """No trustworthy fresh feed verification is currently available."""


def _redis(redis_client=None):
    if redis_client is not None:
        return redis_client
    from .db.redis_client import get_redis_client
    return get_redis_client().client


def _checked_time(value, now):
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SourceVerificationUnavailable("官方来源核验时间无效") from exc
    if checked.tzinfo is None or checked > now:
        raise SourceVerificationUnavailable("官方来源核验时间无效")
    age = (now - checked).total_seconds()
    if age < 0 or age >= _MAX_AGE_SECONDS:
        raise SourceVerificationUnavailable("官方来源核验已过期")
    return age


def cache_verified_feed(records, feed_digest, checked_at, *, redis_client=None, now=None):
    """Store only exact-record digests, never cached job text or scoring facts."""
    now = now or datetime.now(timezone.utc)
    age = _checked_time(checked_at, now)
    if (not isinstance(records, list) or not isinstance(feed_digest, str)
            or not _SHA256.fullmatch(feed_digest)):
        raise SourceVerificationUnavailable("官方来源摘要无效")
    digests = {}
    for record in records:
        if not isinstance(record, dict) or "jobid" not in record:
            raise SourceVerificationUnavailable("官方来源记录无效")
        record_id = str(record["jobid"])
        if record_id in digests:
            digests[record_id] = None  # Duplicate IDs are never verified.
        else:
            digests[record_id] = content_sha256(record)
    payload = {"source_uri": FEED_URI, "feed_sha256": feed_digest,
               "verified_at": checked_at, "record_digests": digests}
    ttl = max(1, _MAX_AGE_SECONDS - int(age))
    try:
        _redis(redis_client).set(_KEY, canonical_json(payload), ex=ttl)
    except Exception as exc:
        raise SourceVerificationUnavailable("官方来源缓存不可用") from exc
    return payload


def read_verified_feed(*, redis_client=None, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        raw = _redis(redis_client).get(_KEY)
        payload = json.loads(raw) if raw is not None else None
        if (not isinstance(payload, dict) or payload.get("source_uri") != FEED_URI
                or not isinstance(payload.get("feed_sha256"), str)
                or not _SHA256.fullmatch(payload["feed_sha256"])
                or not isinstance(payload.get("record_digests"), dict)):
            raise SourceVerificationUnavailable("官方来源暂无有效核验缓存")
        _checked_time(payload.get("verified_at"), now)
        for record_id, digest in payload["record_digests"].items():
            if (not isinstance(record_id, str) or
                    (digest is not None and
                     (not isinstance(digest, str) or not _SHA256.fullmatch(digest)))):
                raise SourceVerificationUnavailable("官方来源核验缓存损坏")
        return payload
    except SourceVerificationUnavailable:
        raise
    except Exception as exc:
        raise SourceVerificationUnavailable("官方来源缓存不可用") from exc


def refresh_verified_feed(*, redis_client=None):
    """Worker-only network refresh; on failure invalidate old data immediately."""
    from .job_catalog_review import fetch_official_feed

    redis = _redis(redis_client)
    try:
        records, digest, checked_at = fetch_official_feed()
        return cache_verified_feed(records, digest, checked_at, redis_client=redis)
    except Exception:
        try:
            redis.delete(_KEY)
        except Exception:
            pass
        raise
