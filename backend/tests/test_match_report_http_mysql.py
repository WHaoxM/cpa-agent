"""HTTP → real MySQL acceptance for verified L3/L4 source gating.

Opt-in: CPA_TEST_LIVE_MYSQL=1 python -B -m pytest backend/tests/test_match_report_http_mysql.py -q
Synthetic owner, portrait and published job are removed by exact owner/job IDs.
"""

import hashlib
import os
import uuid

import pytest
from flask import g, request
from sqlalchemy import event

from app import create_app
from app.api._helpers import Principal
from app.config import Config
from app.services.db.mysql_client import get_mysql_client
from app.services.job_publication import canonical_json, publish_job
from backend.tests.test_verified_match_report import snapshot


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_http_match_report_source_gate_roundtrip_and_revocation(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    db = get_mysql_client()
    tag = "http_l3l4_" + uuid.uuid4().hex[:16]
    sid, jid, l0, l2 = tag, tag + "_job", tag + "_l0", tag + "_l2"
    source_text = "我负责课程团队项目，和组员沟通协作，在紧急截止日前完成交付。"
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    semantic_facts = {
        key: {"value": "中", "evidence": evidence, "source_sha256": source_digest}
        for key, evidence in {
            "communication": "沟通协作", "teamwork": "团队项目",
            "stress_tolerance": "紧急截止日前完成交付", "responsibility": "我负责课程团队项目",
        }.items()
    }
    candidate = {
        "skills": [{"name": "Python", "level": "掌握", "confidence": 1.0}],
        "degree": "专科", "source_sha256": source_digest,
        "semantic_facts": semantic_facts,
    }
    profile = {**candidate, "_resume_confirmation": {"student_id": sid, "task_id": l0}}
    publication_id = None
    report_id = None

    from app.pipeline.entity_extractor.matcher import SkillMatcher
    monkeypatch.setattr(SkillMatcher, "normalizer", property(lambda self: None))

    try:
        db.execute(
            "INSERT INTO student_profile (student_id,profile_json) VALUES (:sid,:payload)",
            {"sid": sid, "payload": canonical_json(profile)}, commit=True,
        )
        db.execute(
            "INSERT INTO student_portrait_4d (student_id,target_job_id,portrait_json) "
            "VALUES (:sid,:jid,:payload)",
            {"sid": sid, "jid": jid, "payload": canonical_json({
                "source_confirmation_task_id": l0, "source_portrait_task_id": l2,
            })}, commit=True,
        )
        portrait_rows = db.fetch_all(
            "SELECT id FROM student_portrait_4d WHERE student_id=:sid AND target_job_id=:jid",
            {"sid": sid, "jid": jid},
        )
        assert len(portrait_rows) == 1
        portrait_id = str(portrait_rows[0]["id"])
        for task_id, pipeline_name, result in (
            (l0, "L0_parse_resume", {"confirmed": True, "candidate_profile": candidate}),
            (l2, "L2_generate_portrait", {
                "portrait_id": portrait_id,
                "portrait": {"source_confirmation_task_id": l0, "source_portrait_task_id": l2},
            }),
        ):
            db.execute(
                "INSERT INTO pipeline_task "
                "(task_id,pipeline,status,student_id,result_json,params_json) "
                "VALUES (:task,:pipeline,'completed',:sid,:result,'{}')",
                {"task": task_id, "pipeline": pipeline_name, "sid": sid,
                 "result": canonical_json(result)}, commit=True,
            )

        published = publish_job(db, job_portrait_id=jid, version="http-test-v1", snapshot=snapshot())
        publication_id = published["id"]

        app = create_app()

        @app.before_request
        def bind_test_principal():
            owner = request.headers.get("X-Test-Owner", sid)
            g.cpa_principal = Principal(owner, owner, "http-test")

        client = app.test_client()
        match_post = client.post("/api/match/run", json={
            "job_portrait_id": jid, "student_id": "forged-owner", "job_skills": ["forged"],
        })
        assert match_post.status_code == 200
        assert match_post.json["status"] == "partial"
        assert match_post.json["success"] is False
        match_result = match_post.json["data"]["result"]
        assert match_result["status"] == "partial"
        assert match_result["match_id"]
        match_read_url = f"/api/match/result/{sid}/{jid}"
        match_read = client.get(match_read_url)
        assert match_read.status_code == 200
        assert match_read.json["success"] is True
        assert match_read.json["status"] == "partial"
        assert match_read.json["data"]["is_matched"] in (False, 0)

        # A legacy skill-gap row can coexist with the L3 result. Its read path
        # must not outlive the publication that justified that result.
        db.execute(
            "INSERT INTO skill_gap (student_id,job_portrait_id,gap_json) "
            "VALUES (:sid,:jid,:gaps)",
            {"sid": sid, "jid": jid, "gaps": '[{"skill":"Python"}]'}, commit=True,
        )

        report_post = client.post("/api/report/generate", json={
            "job_portrait_id": jid, "student_id": "forged-owner", "report_id": "forged-report-id",
        })
        assert report_post.status_code == 200
        assert report_post.json["status"] == "partial"
        assert report_post.json["success"] is False
        report_id = report_post.json["data"]["report_id"]
        assert report_id and report_id != "forged-report-id"
        report_read = client.get(f"/api/report/{report_id}")
        assert report_read.status_code == 200
        assert report_read.json["success"] is True
        assert report_read.json["status"] == "partial"

        assert client.get(match_read_url, headers={"X-Test-Owner": tag + "_other"}).status_code == 403
        assert client.get(f"/api/report/{report_id}",
                          headers={"X-Test-Owner": tag + "_other"}).status_code == 404
        match_rows = db.fetch_all(
            "SELECT id,student_id,job_portrait_id,result_json FROM match_result WHERE student_id=:sid",
            {"sid": sid},
        )
        report_rows = db.fetch_all(
            "SELECT report_id,student_id,status,report_json FROM career_report WHERE student_id=:sid",
            {"sid": sid},
        )
        assert len(match_rows) == len(report_rows) == 1
        assert match_rows[0]["job_portrait_id"] == jid
        assert report_rows[0]["report_id"] == report_id
        assert report_rows[0]["status"] == "partial"

        # A terminal task-write failure must not publish a new match snapshot
        # or orphan a fresh report under a failed L3/L4 task.
        original_match = canonical_json(match_rows[0]["result_json"])
        writes_seen = set()
        def fail_partial_task_update(_conn, _cursor, statement, parameters, _context, _many):
            if "INSERT INTO match_result" in statement:
                writes_seen.add("match_result")
            if "INSERT INTO career_report" in statement:
                writes_seen.add("career_report")
            if "UPDATE pipeline_task SET status" in statement and parameters.get("status") == "partial":
                raise RuntimeError("injected terminal task write failure")

        event.listen(db.engine, "before_cursor_execute", fail_partial_task_update)
        try:
            assert client.post("/api/match/run", json={"job_portrait_id": jid}).status_code == 500
            assert client.post("/api/report/generate", json={"job_portrait_id": jid}).status_code == 500
        finally:
            event.remove(db.engine, "before_cursor_execute", fail_partial_task_update)
        assert writes_seen == {"match_result", "career_report"}
        same_match = db.fetch_all(
            "SELECT result_json FROM match_result WHERE student_id=:sid AND job_portrait_id=:jid",
            {"sid": sid, "jid": jid},
        )
        assert len(same_match) == 1
        assert canonical_json(same_match[0]["result_json"]) == original_match
        assert db.fetch_all(
            "SELECT report_id FROM career_report WHERE student_id=:sid", {"sid": sid}
        ) == [{"report_id": report_id}]
        failed_tasks = db.fetch_all(
            "SELECT task_id FROM pipeline_task WHERE student_id=:sid AND status='failed'",
            {"sid": sid},
        )
        assert len(failed_tasks) == 2
        for failed_task in failed_tasks:
            events = db.fetch_all(
                "SELECT status FROM pipeline_task_event WHERE task_id=:task ORDER BY event_id",
                {"task": failed_task["task_id"]},
            )
            assert [row["status"] for row in events] == ["pending", "running", "failed"]

        # Change only the publication state *after* the normal source check,
        # before the domain write. A stale successful task must not be emitted.
        from app.services import pipeline as pipeline_service
        original_sources = pipeline_service.require_authoritative_match_sources

        def revoke_after_source_check(mysql, student_id, job_id, confirmed_profile):
            checked = original_sources(mysql, student_id, job_id, confirmed_profile)
            db.execute(
                "UPDATE job_catalog_publication SET status='revoked' WHERE id=:id",
                {"id": publication_id}, commit=True,
            )
            return checked

        with monkeypatch.context() as isolated:
            isolated.setattr(pipeline_service, "require_authoritative_match_sources",
                             revoke_after_source_check)
            stale_match = client.post("/api/match/run", json={"job_portrait_id": jid})
        assert stale_match.status_code == 200
        assert stale_match.json["data"]["result"]["reason"] == "catalog_publication_unavailable"
        assert canonical_json(db.fetch_all(
            "SELECT result_json FROM match_result WHERE student_id=:sid AND job_portrait_id=:jid",
            {"sid": sid, "jid": jid},
        )[0]["result_json"]) == original_match

        db.execute(
            "UPDATE job_catalog_publication SET status='published' WHERE id=:id",
            {"id": publication_id}, commit=True,
        )
        with monkeypatch.context() as isolated:
            isolated.setattr(pipeline_service, "require_authoritative_match_sources",
                             revoke_after_source_check)
            stale_report = client.post("/api/report/generate", json={"job_portrait_id": jid})
        assert stale_report.status_code == 200
        assert stale_report.json["data"]["report_id"] is None
        assert db.fetch_all(
            "SELECT report_id FROM career_report WHERE student_id=:sid", {"sid": sid}
        ) == [{"report_id": report_id}]

        db.execute(
            "UPDATE job_catalog_publication SET status='revoked' "
            "WHERE id=:id AND job_portrait_id=:jid",
            {"id": publication_id, "jid": jid}, commit=True,
        )
        assert client.get(match_read_url).status_code == 409
        # Legacy list must not return the persisted score after its source was revoked.
        stale_list = client.get(f"/api/match/list/{sid}")
        assert stale_list.status_code == 409
        assert "total_score" not in stale_list.get_data(as_text=True)
        for route in ("explain", "skill-gap"):
            stale_read = client.get(f"/api/match/{route}/{sid}/{jid}")
            assert stale_read.status_code == 409
            assert stale_read.json["status"] == "stale"
            assert "total_score" not in stale_read.get_data(as_text=True)
            assert "Python" not in stale_read.get_data(as_text=True)
        assert client.get(f"/api/report/{report_id}").status_code == 409

        blocked_match = client.post("/api/match/run", json={"job_portrait_id": jid})
        assert blocked_match.status_code == 200
        assert blocked_match.json["success"] is False
        assert blocked_match.json["status"] == "partial"
        assert blocked_match.json["data"]["result"]["reason"] == "catalog_publication_unavailable"
        assert not blocked_match.json["data"]["result"].get("match_id")

        blocked_report = client.post("/api/report/generate", json={"job_portrait_id": jid})
        assert blocked_report.status_code == 200
        assert blocked_report.json["success"] is False
        assert blocked_report.json["status"] == "partial"
        assert blocked_report.json["data"]["report_id"] is None
        assert db.fetch_all("SELECT report_id FROM career_report WHERE student_id=:sid", {"sid": sid}) == [
            {"report_id": report_id}
        ]
    finally:
        db.execute("DELETE FROM career_report WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM skill_gap WHERE student_id=:sid AND job_portrait_id=:jid",
                   {"sid": sid, "jid": jid}, commit=True)
        db.execute("DELETE FROM match_result WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM pipeline_task_event WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM pipeline_task WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM student_portrait_4d WHERE student_id=:sid AND target_job_id=:jid",
                   {"sid": sid, "jid": jid}, commit=True)
        db.execute("DELETE FROM student_profile WHERE student_id=:sid", {"sid": sid}, commit=True)
        if publication_id is not None:
            db.execute("DELETE FROM job_catalog_publication WHERE id=:id AND job_portrait_id=:jid",
                       {"id": publication_id, "jid": jid}, commit=True)
        for table, key, value in (
            ("career_report", "student_id", sid), ("match_result", "student_id", sid),
            ("skill_gap", "student_id", sid),
            ("pipeline_task", "student_id", sid), ("student_profile", "student_id", sid),
            ("pipeline_task_event", "student_id", sid),
            ("student_portrait_4d", "student_id", sid),
            ("job_catalog_publication", "job_portrait_id", jid),
        ):
            residual = db.fetch_all(f"SELECT COUNT(*) AS n FROM {table} WHERE {key}=:value", {"value": value})
            assert residual == [{"n": 0}], (table, residual)
