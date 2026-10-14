"""Opt-in source-verified, unscored public job directory with real HTTP/DB."""

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires Docker MySQL and Redis and source-listing migration",
)


def test_blocked_model_review_can_be_source_only_but_never_scored(monkeypatch):
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client
    from app.services import job_catalog_review
    from app.services.job_source_verification import (
        _KEY as verification_key, cache_verified_feed,
    )
    from app.services.job_catalog_review import save_review
    from app.services.job_publication import content_sha256
    from app.services import job_source_listing
    from redis import Redis

    nonce = uuid.uuid4().hex[:12]
    jobid = f"{int(nonce[:8], 16)}"
    source_id = f"source_csb_{jobid}"
    review_id = f"jreview_{uuid.uuid4().hex}"
    username = f"source_admin_{nonce}"
    password = "strong-source-admin-password-42"
    enddate = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    record = {"jobid": jobid, "jobname": "Official Source Test Role",
              "entreq": "Bachelor degree or equivalent experience.", "enddate": enddate}
    record_digest = content_sha256(record)
    review = {"review_id": review_id, "source_record_id": jobid,
              "review_version": "source-only-test-v1", "actor": "model",
              "model": "fixture-model", "status": "blocked",
              "blockers": ["unsupported_requirements"],
              "scoring_readiness": False, "publication_authorized": False,
              "source_uri": job_catalog_review.FEED_URI,
              "source_record": record, "source_record_sha256": record_digest,
              "source_license": "DATA.GOV.HK Terms of Use v1.2",
              "candidate_snapshot": None}
    review_digest = content_sha256(review)
    config = type("SourceOnlyCatalogConfig", (Config,), {
        "SECRET_KEY": "only-for-source-only-test-a-long-random-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"source-only-test-{nonce}",
    })
    app = create_app(config)
    db = get_mysql_client()
    redis = Redis.from_url(Config.REDIS_URL)
    previous_cache = redis.get(verification_key)
    previous_cache_ttl = redis.pttl(verification_key)
    save_review(db, review)
    provisioned = app.test_cli_runner().invoke(
        args=["provision-admin", "--username", username],
        input=f"{password}\n{password}\n")
    assert provisioned.exit_code == 0, provisioned.output
    checked_at = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                        lambda: ([record], "f" * 64, checked_at))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    admin, public = requests.Session(), requests.Session()
    listing_id = None
    try:
        admin.trust_env = False
        public.trust_env = False
        token = admin.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        assert admin.post(f"{base}/api/auth/login",
                          json={"username": username, "password": password},
                          headers={"X-CSRFToken": token}, timeout=5).status_code == 200
        route = f"{base}/api/admin/job-source-listings"
        body = {"review_id": review_id, "listing_version": "v1",
                "expected_review_sha256": review_digest,
                "expected_source_record_sha256": record_digest,
                "confirm_source_only": True}
        assert public.post(route, json=body, timeout=5).status_code == 401
        assert admin.post(route, json={**body, "expected_source_record_sha256": "0" * 64},
                          headers={"X-CSRFToken": token}, timeout=5).status_code == 409
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_catalog_source_listing "
                                 "WHERE source_record_id=:rid", {"rid": jobid})["n"] == 0
        monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                            lambda: ([{**record, "entreq": "Changed official condition"}],
                                     "f" * 64, checked_at))
        assert admin.post(route, json=body, headers={"X-CSRFToken": token},
                          timeout=5).status_code == 409
        monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                            lambda: ([record], "f" * 64, checked_at))
        real_audit = job_source_listing._audit
        def fail_audit(*args, **kwargs):
            raise RuntimeError("injected audit failure")
        monkeypatch.setattr(job_source_listing, "_audit", fail_audit)
        failed_audit = admin.post(route, json=body, headers={"X-CSRFToken": token}, timeout=5)
        assert failed_audit.status_code == 500
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_catalog_source_listing "
                                 "WHERE source_record_id=:rid", {"rid": jobid})["n"] == 0
        monkeypatch.setattr(job_source_listing, "_audit", real_audit)
        listed = admin.post(route, json=body, headers={"X-CSRFToken": token}, timeout=5)
        assert listed.status_code == 201, listed.text
        listing_id = listed.json()["id"]
        assert listed.json()["catalog_id"] == source_id
        assert listed.json()["match_eligible"] is False
        assert admin.post(route, json=body, headers={"X-CSRFToken": token},
                          timeout=5).status_code == 409
        row = db.execute_single("SELECT status,content_sha256 FROM job_catalog_source_listing "
                                "WHERE id=:id", {"id": listing_id})
        assert row["status"] == "listed"
        assert row["content_sha256"] == listed.json()["content_sha256"]
        assert db.execute_single("SELECT COUNT(*) AS n FROM admin_audit_log "
                                 "WHERE target_type='job_catalog_source_listing' AND target_id=:id",
                                 {"id": str(listing_id)})["n"] == 1
        assert public.get(route, timeout=5).status_code == 401
        history = admin.get(route, timeout=5)
        assert history.status_code == 200, history.text
        assert any(item["id"] == listing_id and item["scoring_status"] == "not_supported"
                   for item in history.json()["items"])
        admin_detail = admin.get(f"{route}/{listing_id}", timeout=5)
        assert admin_detail.status_code == 200, admin_detail.text
        assert admin_detail.json()["snapshot"]["source_record"] == record
        assert admin_detail.json()["status"] == "listed"
        public_list = public.get(f"{base}/api/profile/job/list", timeout=5)
        assert public_list.status_code == 200, public_list.text
        item = next(item for item in public_list.json()["data"] if item["portrait_id"] == source_id)
        assert item["position_name"] == record["jobname"]
        assert item["publication_status"] == "source_only"
        assert item["match_eligible"] is False
        assert item["scoring_status"] == "not_supported"
        detail = public.get(f"{base}/api/profile/job/{source_id}", timeout=5)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["source_record"]["entreq"] == record["entreq"]
        assert detail.json()["data"]["portrait_json"] is None
        assert any(x["portrait_id"] == source_id for x in public.get(
            f"{base}/api/profile/job/search", params={"keyword": "Official Source Test"},
            timeout=5).json()["data"])
        monkeypatch.setattr(job_catalog_review, "fetch_official_feed",
                            lambda: pytest.fail("public read contacted upstream"))
        assert public.get(f"{base}/api/profile/job/list", timeout=5).status_code == 200
        redis.delete(verification_key)
        partial = public.get(f"{base}/api/profile/job/list", timeout=5)
        assert partial.status_code == 200, partial.text
        assert partial.json()["status"] == "partial"
        assert partial.json()["failed_sources"] == ["official_source_verification"]
        assert source_id not in {x["portrait_id"] for x in partial.json()["data"]}
        assert public.get(f"{base}/api/profile/job/{source_id}", timeout=5).status_code == 503
        cache_verified_feed([record], "f" * 64, datetime.now(timezone.utc).isoformat())
        db.execute_write("UPDATE job_catalog_source_listing SET content_sha256=:digest WHERE id=:id",
                         {"digest": "0" * 64, "id": listing_id})
        assert public.get(f"{base}/api/profile/job/list", timeout=5).status_code == 503
        assert public.get(f"{base}/api/profile/job/{source_id}", timeout=5).status_code == 503
        assert admin.get(route, timeout=5).status_code == 503
        assert admin.get(f"{route}/{listing_id}", timeout=5).status_code == 503
        db.execute_write("UPDATE job_catalog_source_listing SET content_sha256=:digest WHERE id=:id",
                         {"digest": row["content_sha256"], "id": listing_id})

        # An authenticated student is required to try L3; still no task/score.
        student_name = f"source_student_{nonce}"
        student_token = public.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        assert public.post(f"{base}/api/auth/register",
                           json={"username": student_name, "password": password},
                           headers={"X-CSRFToken": student_token}, timeout=5).status_code == 201
        assert public.post(f"{base}/api/auth/login",
                           json={"username": student_name, "password": password},
                           headers={"X-CSRFToken": student_token}, timeout=5).status_code == 200
        student_id = public.get(f"{base}/api/auth/me", timeout=5).json()["user"]["id"]
        before_tasks = db.execute_single("SELECT COUNT(*) AS n FROM pipeline_task WHERE student_id=:sid",
                                         {"sid": student_id})["n"]
        denied = public.post(f"{base}/api/match/run", json={"job_portrait_id": source_id},
                             headers={"X-CSRFToken": student_token}, timeout=5)
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "job_source_only_unscored"
        assert db.execute_single("SELECT COUNT(*) AS n FROM pipeline_task WHERE student_id=:sid",
                                 {"sid": student_id})["n"] == before_tasks
        assert db.execute_single("SELECT COUNT(*) AS n FROM match_result WHERE student_id=:sid",
                                 {"sid": student_id})["n"] == 0
        for endpoint, payload in (
            ("/api/report/generate", {"job_portrait_id": source_id}),
            ("/api/profile/student/pipeline", {"pipeline": "L3_run_match", "job_id": source_id}),
            ("/api/profile/student/pipeline", {"pipeline": "L4_generate_report", "job_id": source_id}),
        ):
            blocked = public.post(f"{base}{endpoint}", json=payload,
                                  headers={"X-CSRFToken": student_token}, timeout=5)
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["code"] == "job_source_only_unscored"
        assert db.execute_single("SELECT COUNT(*) AS n FROM pipeline_task WHERE student_id=:sid",
                                 {"sid": student_id})["n"] == before_tasks
        assert db.execute_single("SELECT COUNT(*) AS n FROM career_report WHERE student_id=:sid",
                                 {"sid": student_id})["n"] == 0

        from app.services.job_source_listing import (
            list_source_only_candidates, read_source_only_candidate,
        )
        after_close = datetime.now(timezone.utc) + timedelta(days=3)
        assert not any(x["portrait_id"] == source_id for x in list_source_only_candidates(
            db, now=after_close))
        assert read_source_only_candidate(db, source_id, now=after_close) is None

        revoked = admin.post(f"{route}/{listing_id}/revoke",
                             json={"expected_content_sha256": row["content_sha256"],
                                   "reason": "test close"},
                             headers={"X-CSRFToken": token}, timeout=5)
        assert revoked.status_code == 200, revoked.text
        assert public.get(f"{base}/api/profile/job/{source_id}", timeout=5).status_code == 404
        assert all(x["portrait_id"] != source_id for x in public.get(
            f"{base}/api/profile/job/list", timeout=5).json()["data"])
        assert admin.get(f"{route}/{listing_id}", timeout=5).json()["status"] == "revoked"
        assert db.execute_single("SELECT COUNT(*) AS n FROM admin_audit_log "
                                 "WHERE target_type='job_catalog_source_listing' AND target_id=:id",
                                 {"id": str(listing_id)})["n"] == 2
    finally:
        if previous_cache is None or previous_cache_ttl <= 0:
            redis.delete(verification_key)
        else:
            redis.set(verification_key, previous_cache, px=previous_cache_ttl)
        redis.close()
        admin.close()
        public.close()
        server.shutdown()
        thread.join(timeout=5)
        if listing_id is not None:
            db.execute_write("DELETE FROM admin_audit_log WHERE target_type='job_catalog_source_listing' "
                             "AND target_id=:id", {"id": str(listing_id)})
        db.execute_write("DELETE FROM job_catalog_source_listing WHERE source_record_id=:rid",
                         {"rid": jobid})
        db.execute_write("DELETE FROM job_catalog_review WHERE review_id=:id", {"id": review_id})
        db.execute_write("DELETE FROM user_account WHERE username IN (:admin,:student)",
                         {"admin": username, "student": f"source_student_{nonce}"})
