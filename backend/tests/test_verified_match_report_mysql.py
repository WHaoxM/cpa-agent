"""Opt-in MySQL write/readback smoke, synthetic fixture with exact-ID cleanup.

CPA_TEST_LIVE_MYSQL=1 python -B -m pytest backend/tests/test_verified_match_report_mysql.py -q
This is not resume extraction or recommendation quality acceptance.
"""
import os
import hashlib
import uuid

import pytest
from flask import g, request

from app import create_app
from app.config import Config
from app.services import pipeline
from app.services.db.mysql_client import get_mysql_client
from app.services.job_publication import canonical_json, publish_job
from backend.tests.test_verified_match_report import snapshot


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_live_publication_match_report_roundtrip(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    db = get_mysql_client()
    tag = "acceptance_" + uuid.uuid4().hex[:16]
    sid, jid, l0, l2 = tag, tag + "_job", tag + "_l0", tag + "_l2"
    # Avoid external entity models; this test exercises actual rule arithmetic
    # and actual MySQL persistence, not semantic model quality.
    from app.pipeline.entity_extractor.matcher import SkillMatcher
    monkeypatch.setattr(SkillMatcher, "normalizer", property(lambda self: None))
    try:
        source_text = "我负责课程团队项目，和组员沟通协作，在紧急截止日前完成交付。"
        source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        semantic_facts = {
            key: {"value": "中", "evidence": evidence, "source_sha256": source_sha256}
            for key, evidence in {
                "communication": "沟通协作", "teamwork": "团队项目",
                "stress_tolerance": "紧急截止日前完成交付", "responsibility": "我负责课程团队项目",
            }.items()
        }
        candidate = {"skills": [{"name": "Python", "level": "掌握", "confidence": 1.0}],
                     "degree": "专科", "source_sha256": source_sha256,
                     "semantic_facts": semantic_facts}
        profile = {**candidate, "_resume_confirmation": {"student_id": sid, "task_id": l0}}
        db.execute("INSERT INTO student_profile (student_id,profile_json) VALUES (:sid,:p)",
                   {"sid": sid, "p": canonical_json(profile)}, commit=True)
        portrait = {"source_confirmation_task_id": l0, "source_portrait_task_id": l2}
        db.execute("INSERT INTO student_portrait_4d (student_id,target_job_id,portrait_json) VALUES (:sid,:jid,:p)",
                   {"sid": sid, "jid": jid, "p": canonical_json(portrait)}, commit=True)
        portrait_id = db.fetch_all("SELECT id FROM student_portrait_4d WHERE student_id=:sid", {"sid": sid})[0]["id"]
        for tid, level, payload in (
            (l0, "L0_parse_resume", {"confirmed": True, "candidate_profile": candidate}),
            (l2, "L2_generate_portrait", {"portrait_id": str(portrait_id), "portrait": portrait}),
        ):
            db.execute("INSERT INTO pipeline_task (task_id,pipeline,status,student_id,result_json,params_json) "
                       "VALUES (:tid,:level,'completed',:sid,:p,'{}')",
                       {"tid": tid, "level": level, "sid": sid, "p": canonical_json(payload)}, commit=True)
        publication = publish_job(db, job_portrait_id=jid, version="test-only-v1", snapshot=snapshot())
        assert publication["job_portrait_id"] == jid
        with pytest.raises(Exception):
            publish_job(db, job_portrait_id=jid, version="test-only-v1", snapshot=snapshot())
        match = pipeline.PipelineRunner().run("L3_run_match", {"student_id": sid, "job_id": jid})
        assert match["status"] == "partial" and match["is_matched"] is False
        row = db.fetch_all("SELECT d1_pass FROM match_result WHERE id=:id AND student_id=:sid",
                           {"id": match["match_id"], "sid": sid})[0]
        assert not row["d1_pass"]  # associate degree cannot pass bachelor's requirement
        report = pipeline.PipelineRunner().run("L4_generate_report", {"student_id": sid, "job_id": jid})
        assert report["status"] == "partial" and report["report_id"]
        app = create_app()

        @app.before_request
        def bind_test_principal():
            owner = request.headers.get("X-Test-Owner", sid)
            g.cpa_principal = {"principal_id": owner, "student_id": owner}

        client = app.test_client()
        match_url = f"/api/match/result/{sid}/{jid}"
        report_url = f"/api/report/{report['report_id']}"
        match_read = client.get(match_url)
        report_read = client.get(report_url)
        assert match_read.status_code == report_read.status_code == 200
        assert match_read.get_json()["status"] == report_read.get_json()["status"] == "partial"
        assert client.get(match_url, headers={"X-Test-Owner": "another_owner"}).status_code == 403
        assert client.get(report_url, headers={"X-Test-Owner": "another_owner"}).status_code == 404
        db.execute("UPDATE job_catalog_publication SET status='revoked' WHERE job_portrait_id=:jid",
                   {"jid": jid}, commit=True)
        stale_match = client.get(match_url)
        stale_report = client.get(report_url)
        assert stale_match.status_code == stale_report.status_code == 409
        assert "total_score" not in stale_match.get_data(as_text=True)
        assert "match_snapshot" not in stale_report.get_data(as_text=True)
        denied = pipeline.PipelineRunner().run("L3_run_match", {"student_id": sid, "job_id": jid})
        assert denied["reason"] == "catalog_publication_unavailable"
        denied_report = pipeline.PipelineRunner().run("L4_generate_report", {"student_id": sid, "job_id": jid})
        assert denied_report["reason"] == "catalog_publication_unavailable"
        assert not denied_report.get("report_id")
        assert db.fetch_all("SELECT COUNT(*) AS n FROM career_report WHERE student_id=:sid",
                            {"sid": sid})[0]["n"] == 1
    finally:
        for table in ("career_report", "match_result", "pipeline_task", "student_portrait_4d", "student_profile"):
            db.execute(f"DELETE FROM {table} WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM job_catalog_publication WHERE job_portrait_id=:jid", {"jid": jid}, commit=True)
