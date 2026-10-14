"""A whitespace-only query must not turn an explicit search into a full catalog dump."""

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from werkzeug.serving import make_server


def test_blank_job_keyword_is_rejected_before_catalog_read(monkeypatch):
    from app import create_app
    from app.api import profile

    calls = []

    def catalog(_mysql):
        calls.append("catalog")
        return {"status": "ready", "items": [{"job_portrait_id": "approved-1",
                                               "position_name": "Analyst"}]}

    def no_database():
        raise AssertionError("blank search must be rejected before database access")

    monkeypatch.setattr(profile, "get_mysql_client", no_database)
    monkeypatch.setattr(profile, "_load_public_job_catalog", catalog)
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = requests.get(f"http://127.0.0.1:{server.server_port}/api/profile/job/search",
                                params={"keyword": " \t "}, timeout=5)
        assert response.status_code == 400, response.text
        assert response.json()["success"] is False
        assert calls == []
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_search_can_page_into_source_only_tier(monkeypatch):
    """A first-page cap must not make later matching informational jobs unreachable."""
    from app import create_app
    from app.api import profile

    published = [{"job_portrait_id": f"published-{n}", "position_name": "Analyst",
                  "publication_status": "published"} for n in range(21)]
    source_only = {"job_portrait_id": "source_csb_123456", "position_name": "Analyst",
                   "publication_status": "source_only", "match_eligible": False,
                   "scoring_status": "not_supported"}
    monkeypatch.setattr(profile, "get_mysql_client", lambda: object())
    monkeypatch.setattr(profile, "_load_public_job_catalog",
                        lambda _mysql: {"status": "ready", "items": [*published, source_only]})
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = requests.get(f"http://127.0.0.1:{server.server_port}/api/profile/job/search",
                                params={"keyword": "Analyst", "limit": 2, "offset": 20}, timeout=5)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 22
        assert body["offset"] == 20
        assert body["has_more"] is False
        assert [item["portrait_id"] for item in body["data"]] == [
            "published-20", "source_csb_123456"]
        assert body["data"][-1]["match_eligible"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires Docker MySQL")
def test_live_search_paginates_published_and_source_only(monkeypatch):
    """Replay the combined directory over a real HTTP socket and persisted rows."""
    from app import create_app
    from app.config import Config
    from app.services import job_catalog_review, job_source_verification
    from app.services.db import get_mysql_client
    from app.services.job_publication import content_sha256
    from redis import Redis

    nonce = uuid.uuid4().hex[:12]
    name = f"Search Pagination QA {nonce}"
    job_id = f"search_pub_{nonce}"
    record_id = str(int(nonce, 16))
    source_id = f"source_csb_{record_id}"
    source = f"{name}. Bachelor required. Python required. No major restriction. No certificates."
    snapshot = {
        "position_name": name, "source_uri": "https://example.test/verified-job",
        "source_text": source, "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_fetched_at": "2026-01-01T00:00:00Z", "source_license": "test-only",
        "reviewed_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": "test-reviewer",
        "review_state": "approved", "required_skills": [{"name": "Python"}],
        "job_profile": {"degree_requirement": "本科", "major_requirements": [],
                        "major_strict": False, "required_certs": []},
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": name},
            "degree_requirement": {"status": "explicit", "excerpt": "Bachelor required"},
            "major_requirements": {"status": "explicit", "excerpt": "No major restriction"},
            "major_strict": {"status": "explicit", "excerpt": "No major restriction"},
            "required_certs": {"status": "explicit", "excerpt": "No certificates"},
            "required_skills": {"status": "explicit", "excerpt": "Python required"},
        },
    }
    now = datetime.now(timezone.utc)
    record = {"jobid": record_id, "jobname": name, "entreq": "test-only qualifications",
              "enddate": (now + timedelta(days=2)).isoformat()}
    source_snapshot = {
        "listing_kind": "source_only", "listing_version": "v1", "source_name": "hk_csb",
        "source_uri": job_catalog_review.FEED_URI, "source_record_id": record_id,
        "source_record": record, "source_record_sha256": content_sha256(record),
        "source_checked_at": now.isoformat(),
        "source_closes_at": record["enddate"], "source_license": "test-only",
        "approved_at": now.isoformat(), "match_eligible": False,
        "scoring_status": "not_supported",
    }
    mysql = get_mysql_client()
    redis = Redis.from_url(Config.REDIS_URL)
    verification_key = f"cpa:catalog:source:csb:verification:v1:test:{nonce}"
    monkeypatch.setattr(job_source_verification, "_KEY", verification_key)
    monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                        lambda: pytest.fail("public search must not fetch the upstream feed"))
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        mysql.execute_write(
            "INSERT INTO job_catalog_publication (job_portrait_id,publication_version,snapshot_json,"
            "content_sha256,status,published_at) VALUES (:id,'v1',:snapshot,:digest,'published',NOW())",
            {"id": job_id, "snapshot": json.dumps(snapshot, ensure_ascii=False),
             "digest": content_sha256(snapshot)},
        )
        mysql.execute_write(
            "INSERT INTO job_catalog_source_listing (source_record_id,listing_version,snapshot_json,"
            "content_sha256,status,listed_at) VALUES (:rid,'v1',:snapshot,:digest,'listed',NOW())",
            {"rid": record_id, "snapshot": json.dumps(source_snapshot, ensure_ascii=False),
             "digest": content_sha256(source_snapshot)},
        )
        job_source_verification.cache_verified_feed(
            [record], "f" * 64, datetime.now(timezone.utc).isoformat(), redis_client=redis,
        )
        assert job_source_verification.read_verified_feed(redis_client=redis)[
            "record_digests"][record_id] == content_sha256(record)
        response = requests.get(f"http://127.0.0.1:{server.server_port}/api/profile/job/search",
                                params={"keyword": name, "limit": 1, "offset": 1}, timeout=10)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert body["has_more"] is False
        assert [row["portrait_id"] for row in body["data"]] == [source_id]
        assert body["data"][0]["match_eligible"] is False
        assert mysql.execute_single("SELECT COUNT(*) AS n FROM job_catalog_publication "
                                    "WHERE job_portrait_id=:id", {"id": job_id})["n"] == 1
        assert mysql.execute_single("SELECT COUNT(*) AS n FROM job_catalog_source_listing "
                                    "WHERE source_record_id=:rid", {"rid": record_id})["n"] == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
        mysql.execute_write("DELETE FROM job_catalog_publication WHERE job_portrait_id=:id",
                            {"id": job_id})
        mysql.execute_write("DELETE FROM job_catalog_source_listing WHERE source_record_id=:rid",
                            {"rid": record_id})
        redis.delete(verification_key)
    assert mysql.execute_single("SELECT COUNT(*) AS n FROM job_catalog_publication "
                                "WHERE job_portrait_id=:id", {"id": job_id})["n"] == 0
    assert mysql.execute_single("SELECT COUNT(*) AS n FROM job_catalog_source_listing "
                                "WHERE source_record_id=:rid", {"rid": record_id})["n"] == 0
    assert redis.get(verification_key) is None
