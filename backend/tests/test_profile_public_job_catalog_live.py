"""HTTP-to-MySQL publication gates for the legacy profile job routes."""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from app import create_app
from app.services.db import get_mysql_client
from app.services.job_publication import content_sha256, publish_job


def _approved_snapshot(name):
    source = f"{name}. Bachelor required. Python required. No major restriction. No certificates."
    return {
        "position_name": name,
        "source_uri": "https://example.test/verified-job",
        "source_text": source,
        "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_fetched_at": "2026-01-01T00:00:00Z",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": "test-reviewer",
        "source_license": "test-only",
        "review_state": "approved",
        "required_skills": [{"name": "Python"}],
        "job_profile": {
            "degree_requirement": "本科",
            "major_requirements": [],
            "major_strict": False,
            "required_certs": [],
        },
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": name},
            "degree_requirement": {"status": "explicit", "excerpt": "Bachelor required"},
            "major_requirements": {"status": "explicit", "excerpt": "No major restriction"},
            "major_strict": {"status": "explicit", "excerpt": "No major restriction"},
            "required_certs": {"status": "explicit", "excerpt": "No certificates"},
            "required_skills": {"status": "explicit", "excerpt": "Python required"},
        },
    }


def _portrait(mysql, portrait_id, name):
    mysql.execute_write(
        "INSERT INTO job_portrait_4d "
        "(portrait_id, position_name, jd_count, required_degree, required_skill_cnt, portrait_json) "
        "VALUES (:id, :name, 1, '本科', 1, :json)",
        {"id": portrait_id, "name": name, "json": json.dumps({"test": True})},
    )


def test_profile_job_routes_only_expose_latest_approved_publications():
    mysql = get_mysql_client()
    suffix = uuid.uuid4().hex[:8]
    published_id, unpublished_id = "profile_pub_" + suffix, "profile_unpub_" + suffix
    published_name, unpublished_name = "Verified Catalog QA " + suffix, "Unreviewed Catalog QA " + suffix
    published_version = "v1-" + suffix
    app = create_app()
    client = app.test_client()
    try:
        _portrait(mysql, published_id, published_name)
        _portrait(mysql, unpublished_id, unpublished_name)
        mysql.execute_write(
            "INSERT INTO job_posting "
            "(job_id, position_name, job_category, company_name, posting_json, is_it_position) "
            "VALUES (:id, :name, :category, :company, :json, 1)",
            {
                "id": "raw_" + suffix,
                "name": unpublished_name,
                "category": "UNPUBLISHED_CATEGORY_" + suffix,
                "company": "UNPUBLISHED_COMPANY_" + suffix,
                "json": json.dumps({"test": True}),
            },
        )
        publish_job(
            mysql,
            job_portrait_id=published_id,
            version=published_version,
            snapshot=_approved_snapshot(published_name),
        )

        listing = client.get("/api/profile/job/list?limit=20").get_json()
        listed_ids = {row.get("portrait_id", row.get("job_portrait_id")) for row in listing["data"]}
        listing_has_published = published_id in listed_ids
        listing_leaks_unpublished = unpublished_id in listed_ids
        detail = client.get(f"/api/profile/job/{published_id}")
        published_detail_ok = (
            detail.status_code == 200
            and detail.get_json()["data"]["position_name"] == published_name
        )
        unpublished_detail_status = client.get(f"/api/profile/job/{unpublished_id}").status_code

        search = client.get(
            "/api/profile/job/search", query_string={"keyword": suffix, "limit": 20}
        ).get_json()
        search_text = json.dumps(search, ensure_ascii=False)
        search_has_published = published_name in search_text
        search_leaks_unpublished = unpublished_name in search_text
        search_leaks_company = "UNPUBLISHED_COMPANY_" + suffix in search_text

        categories = client.get("/api/profile/job/categories").get_json()
        category_text = json.dumps(categories, ensure_ascii=False)
        category_leaks_unpublished = "UNPUBLISHED_CATEGORY_" + suffix in category_text
        assert (
            listing_has_published
            and not listing_leaks_unpublished
            and published_detail_ok
            and unpublished_detail_status == 404
            and search_has_published
            and not search_leaks_unpublished
            and not search_leaks_company
            and not category_leaks_unpublished
            and categories["reason"] == "published_catalog_has_no_category_field"
        ), {
            "listing_has_published": listing_has_published,
            "listing_leaks_unpublished": listing_leaks_unpublished,
            "published_detail_ok": published_detail_ok,
            "unpublished_detail_status": unpublished_detail_status,
            "search_has_published": search_has_published,
            "search_leaks_unpublished": search_leaks_unpublished,
            "search_leaks_company": search_leaks_company,
            "category_leaks_unpublished": category_leaks_unpublished,
            "category_reason": categories.get("reason"),
        }

        # The newest revoked version must hide the older approved publication.
        revoked_snapshot = _approved_snapshot(published_name + " revised")
        mysql.execute_write(
            "INSERT INTO job_catalog_publication "
            "(job_portrait_id, publication_version, snapshot_json, content_sha256, status, published_at) "
            "VALUES (:id, :version, :snapshot, :digest, 'revoked', NOW())",
            {
                "id": published_id,
                "version": "v2-" + suffix,
                "snapshot": json.dumps(revoked_snapshot, ensure_ascii=False),
                "digest": content_sha256(revoked_snapshot),
            },
        )
        after_revoke = client.get("/api/profile/job/list?limit=20").get_json()
        after_revoke_ids = {
            row.get("portrait_id", row.get("job_portrait_id")) for row in after_revoke["data"]
        }
        assert published_id not in after_revoke_ids
        assert client.get(f"/api/profile/job/{published_id}").status_code == 404
    finally:
        mysql.execute_write(
            "DELETE FROM job_catalog_publication WHERE job_portrait_id IN (:published, :unpublished)",
            {"published": published_id, "unpublished": unpublished_id},
        )
        mysql.execute_write("DELETE FROM job_portrait_4d WHERE portrait_id IN (:published, :unpublished)",
                            {"published": published_id, "unpublished": unpublished_id})
        mysql.execute_write("DELETE FROM job_posting WHERE job_id=:job_id", {"job_id": "raw_" + suffix})
