"""Public reads use only a bounded shared source verification cache."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import job_catalog_review, job_source_verification as verification
from app.services.job_publication import content_sha256


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = []

    def set(self, key, value, ex=None):
        self.values[key] = value
        self.ttls.append(ex)

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


def test_verification_expires_within_five_minutes_and_duplicate_ids_fail_closed():
    cache = MemoryRedis()
    now = datetime.now(timezone.utc)
    first = {"jobid": "42", "jobname": "First"}
    duplicate = {"jobid": "42", "jobname": "Second"}
    payload = verification.cache_verified_feed(
        [first, duplicate], "f" * 64, now.isoformat(), redis_client=cache, now=now)
    assert payload["record_digests"]["42"] is None
    assert 1 <= cache.ttls[-1] <= 300
    assert verification.read_verified_feed(redis_client=cache, now=now)["record_digests"]["42"] is None
    with pytest.raises(verification.SourceVerificationUnavailable, match="已过期"):
        verification.read_verified_feed(redis_client=cache, now=now + timedelta(seconds=300))


def test_failed_background_refresh_removes_prior_snapshot(monkeypatch):
    cache = MemoryRedis()
    record = {"jobid": "42", "jobname": "First"}
    verification.cache_verified_feed([record], "f" * 64,
                                     datetime.now(timezone.utc).isoformat(), redis_client=cache)
    assert verification.read_verified_feed(redis_client=cache)["record_digests"]["42"] == content_sha256(record)

    def offline():
        raise OSError("upstream offline")

    monkeypatch.setattr(job_catalog_review, "fetch_official_feed", offline)
    with pytest.raises(OSError, match="upstream offline"):
        verification.refresh_verified_feed(redis_client=cache)
    with pytest.raises(verification.SourceVerificationUnavailable, match="暂无有效"):
        verification.read_verified_feed(redis_client=cache)


def test_registered_worker_task_refreshes_then_invalidates_on_upstream_failure(monkeypatch):
    from app.services.job_source_verification_jobs import refresh_source_feed_task

    cache = MemoryRedis()
    record = {"jobid": "42", "jobname": "First"}
    monkeypatch.setattr(verification, "_redis", lambda *_args: cache)
    monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                        lambda: ([record], "f" * 64,
                                 datetime.now(timezone.utc).isoformat()))
    assert refresh_source_feed_task()["status"] == "ready"
    assert verification.read_verified_feed()["record_digests"]["42"] == content_sha256(record)

    def offline():
        raise OSError("upstream offline")

    monkeypatch.setattr(job_catalog_review, "fetch_official_feed", offline)
    assert refresh_source_feed_task()["status"] == "unavailable"
    with pytest.raises(verification.SourceVerificationUnavailable):
        verification.read_verified_feed()


def test_scored_public_catalog_survives_source_cache_outage_without_network(monkeypatch):
    from app import create_app
    from app.api import profile

    scored = {"job_portrait_id": "published-1", "position_name": "Published Role",
              "publication_status": "published", "content_sha256": "a" * 64}
    monkeypatch.setattr(profile, "get_mysql_client", lambda: object())
    monkeypatch.setattr(profile, "_load_published_job_catalog",
                        lambda _mysql: {"status": "ready", "items": [scored]})

    def no_cache(_mysql):
        raise verification.SourceVerificationUnavailable("cache missing")

    monkeypatch.setattr(profile, "list_source_only_candidates", no_cache)
    monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                        lambda: pytest.fail("public HTTP fetched upstream"))
    client = create_app().test_client()
    for path in ("/api/profile/job/list", "/api/profile/job/search?keyword=Published"):
        response = client.get(path)
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "partial"
        assert body["failed_sources"] == ["official_source_verification"]
        assert body["retry_after_seconds"] == 60
        assert [row["portrait_id"] for row in body["data"]] == ["published-1"]


def test_celery_periodic_refresh_is_registered_on_worker_queue():
    from app.services.agent_runtime.media.jobs import PIPELINE_QUEUE, celery_app

    beat = celery_app.conf.beat_schedule["official-source-refresh"]
    assert beat["task"] == "cpa.catalog.refresh_source_feed"
    assert beat["schedule"] == 60
    assert beat["options"]["queue"] == PIPELINE_QUEUE
    assert "app.services.job_source_verification_jobs" in celery_app.conf.imports
