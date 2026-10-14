"""Reserved informational IDs cannot enter the scored publication store."""

import pytest
from datetime import datetime, timezone

from app.services.job_publication import content_sha256
from app.services import job_catalog_review
from app.services.admin_job_publication import prepare_candidate
from app.services.job_publication import publish_job, read_published_job
from app.services.job_source_listing import (
    is_source_only_id,
    list_source_only_candidates,
    read_source_only_candidate,
)
from app.services import job_source_verification
from app.services.job_source_verification import SourceVerificationUnavailable


class _MemoryRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex=None):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


def test_source_only_prefix_is_reserved_across_publication_writers():
    source_id = "source_csb_50368"
    assert is_source_only_id(source_id)
    assert not is_source_only_id("ordinary_50368")
    with pytest.raises(ValueError, match="保留岗位 ID"):
        prepare_candidate(job_portrait_id=source_id, publication_version="v1",
                          candidate={}, actor_id="admin")
    with pytest.raises(ValueError, match="保留岗位 ID"):
        publish_job(None, job_portrait_id=source_id, version="v1", snapshot={})
    assert read_published_job(None, source_id) is None


def test_public_source_listing_is_hidden_when_official_record_changes(monkeypatch):
    """Do not keep serving the approved snapshot after the authoritative feed changes."""
    original = {"jobid": "50368", "jobname": "Original role",
                "entreq": "Original qualifications", "enddate": "2099-12-31T23:59:00+08:00"}
    changed = {**original, "entreq": "Changed qualifications"}
    digest = content_sha256(original)
    snapshot = {
        "listing_kind": "source_only", "listing_version": "v1",
        "source_name": "hk_csb", "source_uri": job_catalog_review.FEED_URI,
        "source_record_id": "50368", "source_record": original,
        "source_record_sha256": digest, "source_checked_at": "2026-10-13T00:00:00+00:00",
        "source_closes_at": "2099-12-31T23:59:00+08:00",
        "source_license": "test", "approved_at": "2026-10-13T00:00:00+00:00",
        "match_eligible": False, "scoring_status": "not_supported",
    }
    row = {"id": 1, "source_record_id": "50368", "listing_version": "v1",
           "snapshot_json": snapshot, "content_sha256": content_sha256(snapshot),
           "status": "listed"}

    class FakeMysql:
        def execute(self, *_args, **_kwargs):
            return [row]

        def execute_single(self, *_args, **_kwargs):
            return row

    checked_at = datetime.now(timezone.utc).isoformat()
    cache = _MemoryRedis()
    monkeypatch.setattr(job_source_verification, "_redis", lambda *_args: cache)
    monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                        lambda: ([changed], "f" * 64, checked_at))
    job_source_verification.refresh_verified_feed()
    mysql = FakeMysql()
    assert list_source_only_candidates(mysql) == []
    assert read_source_only_candidate(mysql, "source_csb_50368") is None

    monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                        lambda: ([original], "f" * 64, checked_at))
    job_source_verification.refresh_verified_feed()
    visible = list_source_only_candidates(mysql)
    assert visible[0]["source_fetched_at"] == snapshot["source_checked_at"]
    assert visible[0]["source_verified_at"] == checked_at
    assert read_source_only_candidate(mysql, "source_csb_50368")["source_verified_at"] == checked_at

    def unavailable():
        raise OSError("upstream unavailable")

    monkeypatch.setattr(job_catalog_review, "fetch_official_feed", unavailable)
    with pytest.raises(OSError, match="upstream unavailable"):
        job_source_verification.refresh_verified_feed()
    with pytest.raises(SourceVerificationUnavailable, match="暂无有效核验缓存"):
        list_source_only_candidates(mysql)
    with pytest.raises(SourceVerificationUnavailable, match="暂无有效核验缓存"):
        read_source_only_candidate(mysql, "source_csb_50368")
