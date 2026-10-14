"""Read-side publication view: latest-version and revocation semantics."""

import hashlib
import os
from datetime import datetime, timezone

import pytest

from app.services.job_catalog_listing import list_published_job_candidates
from app.services.job_publication import content_sha256


def _snapshot(name):
    source = f"{name}. Bachelor required. Python required. No major restriction. No certificates."
    return {"position_name": name, "source_uri": "https://example.com/test-job",
            "source_text": source, "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "source_fetched_at": "2026-10-08T00:00:00Z", "source_license": "test-only",
            "reviewed_at": "2026-10-09T00:00:00Z", "reviewed_by": "test",
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
            }}


class _DB:
    def __init__(self):
        self.rows = []

    def add(self, job, version, name, *, status="published"):
        snap = _snapshot(name)
        self.rows.append({"id": len(self.rows) + 1, "job_portrait_id": job,
                          "publication_version": version, "snapshot_json": snap,
                          "content_sha256": content_sha256(snap), "status": status,
                          "published_at": datetime(2026, 10, 9, tzinfo=timezone.utc)})

    def fetch_all(self, sql, params=None):
        if "WHERE job_portrait_id=:jid" in sql:
            rows = [r for r in self.rows if r["job_portrait_id"] == params["jid"]]
            if params.get("version"):
                rows = [r for r in rows if r["publication_version"] == params["version"]]
            return sorted(rows, key=lambda r: r["id"], reverse=True)[:1]
        if "row_count" in sql:
            return [{"row_count": len(self.rows), "max_id": len(self.rows),
                     "published_id_sum": sum(r["id"] for r in self.rows if r["status"] == "published"),
                     "revoked_id_sum": sum(r["id"] for r in self.rows if r["status"] == "revoked")}]
        latest = {}
        for row in self.rows:
            latest[row["job_portrait_id"]] = row
        current = sorted((r for r in latest.values() if r["status"] == "published"),
                         key=lambda r: r["id"], reverse=True)
        if "COUNT(*) AS n" in sql:
            return [{"n": len(current), "latest_published_at":
                     max((r["published_at"] for r in current), default=None)}]
        if "latest.latest_id=p.id" in sql:
            return current[params["offset"]:params["offset"] + params["limit"]]
        raise AssertionError(sql)


def test_empty_then_new_publication_refreshes_without_report_rebuild():
    db = _DB()
    empty = list_published_job_candidates(db)
    assert empty["status"] == "empty" and empty["catalog_version"]
    db.add("job-1", "v1", "Intern Engineer")
    ready = list_published_job_candidates(db)
    assert ready["status"] == "ready" and ready["total"] == 1
    assert ready["catalog_version"] != empty["catalog_version"]
    item = ready["items"][0]
    assert item["vacancy_validity"] == "unknown"  # publication != currently hiring
    assert item["source_uri"].startswith("https://")


def test_latest_revoked_never_falls_back_to_old_publication():
    db = _DB()
    db.add("job-1", "v1", "Old")
    first = list_published_job_candidates(db)
    db.add("job-1", "v2", "New", status="revoked")
    after = list_published_job_candidates(db)
    assert after["status"] == "empty" and after["items"] == []
    assert after["catalog_version"] != first["catalog_version"]


def test_catalog_change_during_nonempty_page_is_not_returned_as_current():
    class ConcurrentDB(_DB):
        injected = False

        def fetch_all(self, sql, params=None):
            result = super().fetch_all(sql, params)
            if "latest.latest_id=p.id" in sql and not self.injected:
                self.injected = True
                # Simulate a separate operator publishing another job after
                # the page query but before the page has been fully verified.
                self.add("job-2", "v1", "Concurrent role")
            return result

    db = ConcurrentDB()
    db.add("job-1", "v1", "Initial role")
    result = list_published_job_candidates(db)
    assert result["status"] == "unavailable"
    assert result["reason"] == "catalog_changed_during_read"
    assert result["items"] == []


def test_bounded_page_and_integrity_failure():
    db = _DB()
    db.add("job-1", "v1", "One")
    db.add("job-2", "v1", "Two")
    result = list_published_job_candidates(db, limit=1)
    assert result["total"] == 2 and result["has_more"] and len(result["items"]) == 1
    assert list_published_job_candidates(db, limit=1, offset=1)["has_more"] is False
    db.rows[-1]["snapshot_json"]["position_name"] = "tampered"
    result = list_published_job_candidates(db)
    assert result["status"] == "unavailable" and result["items"] == []


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="opt-in live MySQL read")
def test_live_catalog_contract():
    from app.services.db.mysql_client import get_mysql_client
    result = list_published_job_candidates(get_mysql_client(), limit=5)
    assert result["status"] in {"ready", "empty"}
    assert result["catalog_version"] is not None
    assert len(result["items"]) <= 5
