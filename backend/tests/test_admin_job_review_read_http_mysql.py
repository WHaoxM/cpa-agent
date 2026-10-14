"""Opt-in account-gated HTTP read of persisted model job reviews."""

import os
import threading
import uuid
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
import requests
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.environ.get("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires Docker MySQL and Redis",
)


def _ready_candidate(nonce):
    source = ("Test-only role. Required Python skill. Bachelor's degree required. "
              "No major restriction. No certificates required. Synthetic fixture, not a real job.")
    return {
        "position_name": "Test-only role",
        "source_uri": f"https://example.test/jobs/{nonce}",
        "source_text": source,
        "source_text_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "source_fetched_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "source_closes_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "source_license": "test-fixture",
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": "Test-only role"},
            "degree_requirement": {"status": "explicit", "excerpt": "Bachelor's degree required"},
            "major_requirements": {"status": "explicit", "excerpt": "No major restriction"},
            "major_strict": {"status": "explicit", "excerpt": "No major restriction"},
            "required_certs": {"status": "explicit", "excerpt": "No certificates required"},
            "required_skills": {"status": "explicit", "excerpt": "Required Python skill"},
        },
        "required_skills": [{"name": "Python", "level": "必须"}],
        "job_profile": {"degree_requirement": "本科", "major_requirements": [],
                        "major_strict": False, "required_certs": []},
    }


def test_model_review_is_readable_for_account_admin_but_never_an_approval():
    from app import create_app
    from app.config import Config
    from app.services.db import get_mysql_client
    from app.services.job_catalog_review import save_review

    nonce = uuid.uuid4().hex[:12]
    admin_name = f"review_admin_{nonce}"
    student_name = f"review_student_{nonce}"
    password = "strong-review-test-password-42"
    review_id = f"jreview_{uuid.uuid4().hex}"
    ready_id = f"jreview_{uuid.uuid4().hex}"
    source_id = f"fixture_{nonce}"
    config = type("AdminReviewReadConfig", (Config,), {
        "SECRET_KEY": "only-for-admin-review-read-test-a-long-random-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "RATELIMIT_KEY_PREFIX": f"review-read-test-{nonce}",
    })
    app = create_app(config)
    db = get_mysql_client()
    review = {
        "review_id": review_id,
        "source_record_id": source_id,
        "review_version": "test-review-v1",
        "actor": "model",
        "model": "fixture-model",
        "status": "blocked",
        "blockers": ["unsupported_requirements"],
        "scoring_readiness": False,
        "publication_authorized": False,
        "source_uri": "https://example.test/feed",
        "source_text": "Synthetic job with unresolved alternative qualifications.",
        "candidate_snapshot": None,
    }
    save_review(db, review)
    candidate = _ready_candidate(nonce)
    ready_review = {**review, "review_id": ready_id,
                    "source_record_id": f"ready_{nonce}",
                    "status": "candidate_ready", "blockers": [],
                    "scoring_readiness": True,
                    "candidate_snapshot": {**candidate, "reviewed_by": "model:fixture-model",
                                           "reviewed_at": datetime.now(timezone.utc).isoformat(),
                                           "review_state": "pending", "review_actor": "model",
                                           "review_id": ready_id}}
    save_review(db, ready_review)
    provisioned = app.test_cli_runner().invoke(
        args=["provision-admin", "--username", admin_name],
        input=f"{password}\n{password}\n",
    )
    assert provisioned.exit_code == 0, provisioned.output
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    admin, student, stranger = requests.Session(), requests.Session(), requests.Session()
    try:
        for browser in (admin, student, stranger):
            browser.trust_env = False
        student_csrf = student.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        assert student.post(f"{base}/api/auth/register",
                            json={"username": student_name, "password": password},
                            headers={"X-CSRFToken": student_csrf}, timeout=5).status_code == 201
        assert student.post(f"{base}/api/auth/login",
                            json={"username": student_name, "password": password},
                            headers={"X-CSRFToken": student_csrf}, timeout=5).status_code == 200
        admin_csrf = admin.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        assert admin.post(f"{base}/api/auth/login",
                          json={"username": admin_name, "password": password},
                          headers={"X-CSRFToken": admin_csrf}, timeout=5).status_code == 200
        route = f"{base}/api/admin/job-reviews"
        assert stranger.get(route, timeout=5).status_code == 401
        assert student.get(route, headers={"X-Admin": "true"}, timeout=5).status_code == 403
        listing = admin.get(route, params={"status": "blocked", "limit": 100}, timeout=5)
        assert listing.status_code == 200, listing.text
        assert admin.get(route, params={"status": "approved"}, timeout=5).status_code == 400
        assert admin.get(route, params={"limit": "not-a-number"}, timeout=5).status_code == 400
        assert admin.get(f"{route}/jreview_missing_{nonce}", timeout=5).status_code == 404
        matches = [row for row in listing.json()["items"] if row["review_id"] == review_id]
        assert len(matches) == 1
        assert matches[0]["publication_authorized"] is False
        assert matches[0]["blockers"] == ["unsupported_requirements"]
        detail = admin.get(f"{route}/{review_id}", timeout=5)
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["review"]["source_text"] == review["source_text"]
        assert payload["publication_authorized"] is False
        assert payload["candidate_for_preview"] is None
        assert payload["source_current"] == "unknown"
        assert student.get(f"{route}/{review_id}", timeout=5).status_code == 403
        ready_listing = admin.get(route, params={"status": "candidate_ready"}, timeout=5)
        assert ready_listing.status_code == 200, ready_listing.text
        assert any(item["review_id"] == ready_id for item in ready_listing.json()["items"])
        ready_detail = admin.get(f"{route}/{ready_id}", timeout=5)
        assert ready_detail.status_code == 200, ready_detail.text
        review_candidate = ready_detail.json()["candidate_for_preview"]
        assert review_candidate == candidate
        assert ready_detail.json()["publication_authorized"] is False
        preview = admin.post(f"{base}/api/admin/job-publications/preview",
                             json={"job_portrait_id": f"review_job_{nonce}",
                                   "publication_version": "v1", "snapshot": review_candidate},
                             headers={"X-CSRFToken": admin_csrf}, timeout=5)
        assert preview.status_code == 200, preview.text
        assert preview.json()["status"] == "preview_not_approved"
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_catalog_publication WHERE job_portrait_id=:id",
                                 {"id": f"review_job_{nonce}"})["n"] == 0
        db.execute_write("UPDATE job_catalog_review SET review_json=JSON_SET(review_json, "
                         "'$.source_text', 'tampered') WHERE review_id=:id", {"id": review_id})
        assert admin.get(f"{route}/{review_id}", timeout=5).status_code == 503
    finally:
        for browser in (admin, student, stranger):
            browser.close()
        server.shutdown()
        thread.join(timeout=5)
        db.execute_write("DELETE FROM job_catalog_review WHERE review_id=:id", {"id": review_id})
        db.execute_write("DELETE FROM job_catalog_review WHERE review_id=:id", {"id": ready_id})
        db.execute_write("DELETE FROM user_account WHERE username IN (:a,:s)",
                         {"a": admin_name, "s": student_name})
        assert db.execute_single("SELECT COUNT(*) AS n FROM job_catalog_review WHERE review_id=:id",
                                 {"id": review_id})["n"] == 0
