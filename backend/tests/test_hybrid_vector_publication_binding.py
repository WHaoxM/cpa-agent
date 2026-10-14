"""A JD vector hit belongs to one exact public publication, not just its job ID."""

import json

from app.services import hybrid_retrieve


class FakeMysql:
    pass


def _publication():
    return {
        "publication_version": "v2",
        "content_sha256": "a" * 64,
        "snapshot_json": {
            "position_name": "Verified Role",
            "source_uri": "https://example.test/job",
            "required_skills": [{"name": "Python"}],
        },
    }


def test_hybrid_vector_requires_exact_publication_binding(monkeypatch):
    from app.services import job_publication

    monkeypatch.setattr(job_publication, "read_published_job",
                        lambda mysql, job_id: _publication() if job_id == "job-1" else None)
    stale = [
        {"id": "job-1", "job_portrait_id": "job-1", "channel": "vector"},
        {"id": "job-1", "job_portrait_id": "job-1", "channel": "vector",
         "publication_version": "v1", "content_sha256": "a" * 64},
        {"id": "job-1", "job_portrait_id": "job-1", "channel": "vector",
         "publication_version": "v2", "content_sha256": "b" * 64},
    ]
    valid = {"id": "job-1", "job_portrait_id": "job-1", "channel": "vector",
         "publication_version": "v2", "content_sha256": "a" * 64,
         "position_name": "FORGED PRIVATE TITLE", "text": "PRIVATE"}
    assert hybrid_retrieve._safe_retrieval_hits(FakeMysql(), stale, ["Python"]) == []
    safe = hybrid_retrieve._safe_retrieval_hits(FakeMysql(), stale + [valid], ["Python"])
    assert len(safe) == 1
    assert safe[0]["position_name"] == "Verified Role"
    assert safe[0]["publication_version"] == "v2"
    assert "PRIVATE" not in json.dumps(safe)


def test_public_jd_vector_search_requires_exact_publication_binding(monkeypatch):
    from app.services import job_catalog_listing, job_publication

    monkeypatch.setattr(job_catalog_listing, "list_published_job_candidates",
                        lambda mysql, **kwargs: {"status": "ok"})
    monkeypatch.setattr(job_publication, "read_published_job",
                        lambda mysql, job_id: _publication() if job_id == "job-1" else None)
    raw = [
        {"metadata": json.dumps({"job_id": "job-1"}), "text": "PRIVATE LEGACY"},
        {"metadata": json.dumps({"job_portrait_id": "job-1",
                                  "publication_version": "v1",
                                  "content_sha256": "a" * 64})},
        {"metadata": json.dumps({"job_portrait_id": "job-1",
                                  "publication_version": "v2",
                                  "content_sha256": "b" * 64})},
        {"metadata": json.dumps({"job_portrait_id": "job-1",
                                  "publication_version": "v2",
                                  "content_sha256": "a" * 64}),
         "text": "PRIVATE CURRENT", "distance": 0.25},
    ]
    projected = hybrid_retrieve.project_public_jd_vector_hits(FakeMysql(), raw)
    assert len(projected) == 1
    assert projected[0]["position_name"] == "Verified Role"
    assert projected[0]["distance"] == 0.25
    assert "PRIVATE" not in json.dumps(projected)
