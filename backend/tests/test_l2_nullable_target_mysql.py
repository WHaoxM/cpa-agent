"""Opt-in HTTP/MySQL regression for repeated untargeted L2 portrait generation.

The scored portrait is a deterministic stand-in; task and portrait persistence are real.
Run with CPA_TEST_LIVE_MYSQL=1 from the repository root.
"""

import json
import os
import uuid

import pytest
from flask import g
from sqlalchemy import event

from app import create_app
from app.api._helpers import Principal
from app.config import Config
from app.services.db.mysql_client import get_mysql_client


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
@pytest.mark.parametrize("targeted", [False, True])
def test_repeated_l2_updates_one_authoritative_portrait_and_failed_task_rolls_back(monkeypatch, targeted):
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    sid = "l2_null_" + uuid.uuid4().hex[:16]
    l0 = "task_" + uuid.uuid4().hex[:20]
    jid = "job_" + uuid.uuid4().hex[:16] if targeted else None
    body = {"pipeline": "L2_generate_portrait"}
    if jid:
        body["job_id"] = jid
    candidate = {"skills": [{"name": "Python", "level": "掌握"}], "degree": "本科"}
    profile = {**candidate, "_resume_confirmation": {"student_id": sid, "task_id": l0}}
    mysql = get_mysql_client()
    calls = []

    def scored_portrait(_self, *, student_id, skills, match_job_id, student_profile):
        calls.append(student_id)
        assert student_id == sid and match_job_id == jid
        assert skills == candidate["skills"] and student_profile == profile
        score = 60 + len(calls)
        return {
            "status": "completed",
            "D1_basic": {"dimension_score": score},
            "D2_skill": {"dimension_score": 70, "skill_coverage": 0.8},
            "D3_quality": {"dimension_score": 65},
            "D4_potential": {"dimension_score": 67},
            "summary": {"competitiveness_score": score,
                        "competitiveness_level": "中等", "completeness_score": 80},
        }

    monkeypatch.setattr(PortraitGenerator, "generate_student_portrait", scored_portrait)
    try:
        mysql.execute(
            "INSERT INTO student_profile(student_id,profile_json) VALUES (:sid,:profile)",
            {"sid": sid, "profile": json.dumps(profile, ensure_ascii=False)}, commit=True,
        )
        mysql.execute(
            "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,params_json,result_json) "
            "VALUES (:task,'L0_parse_resume','completed',:sid,'{}',:result)",
            {"task": l0, "sid": sid, "result": json.dumps({
                "confirmed": True, "candidate_profile": candidate}, ensure_ascii=False)}, commit=True,
        )

        app = create_app()

        @app.before_request
        def bind_principal():
            g.cpa_principal = Principal(sid, sid, "live-test")

        client = app.test_client()
        first = client.post("/api/profile/student/pipeline", json=body)
        second = client.post("/api/profile/student/pipeline", json=body)
        assert first.status_code == second.status_code == 200
        assert first.json["data"]["status"] == second.json["data"]["status"] == "completed"
        rows = mysql.fetch_all(
            "SELECT id, portrait_json, competitiveness FROM student_portrait_4d "
            "WHERE student_id=:sid AND target_job_id <=> :jid ORDER BY id",
            {"sid": sid, "jid": jid},
        )
        assert len(rows) == 1
        assert str(rows[0]["id"]) == first.json["data"]["portrait_id"] == second.json["data"]["portrait_id"]
        assert rows[0]["competitiveness"] == 62
        payload = rows[0]["portrait_json"]
        payload = json.loads(payload) if isinstance(payload, str) else payload
        assert payload["source_portrait_task_id"] == second.json["data"]["task_id"]
        assert len(calls) == 2
        completed_events = mysql.fetch_all(
            "SELECT status FROM pipeline_task_event WHERE task_id=:task ORDER BY event_id",
            {"task": second.json["data"]["task_id"]},
        )
        assert [row["status"] for row in completed_events] == ["pending", "running", "completed"]

        # A terminal task write failure must not replace the last verified
        # portrait with data attributed to a failed task.
        def fail_completed_update(_conn, _cursor, statement, parameters, _context, _executemany):
            if ("UPDATE pipeline_task SET status" in statement
                    and ("status='completed'" in statement
                         or parameters.get("status") == "completed")
                    and parameters.get("task_id") != l0):
                raise RuntimeError("injected L2 completion write failure")

        event.listen(mysql.engine, "before_cursor_execute", fail_completed_update)
        try:
            failed = client.post("/api/profile/student/pipeline", json=body)
        finally:
            event.remove(mysql.engine, "before_cursor_execute", fail_completed_update)
        assert failed.status_code == 500
        after = mysql.fetch_all(
            "SELECT id, portrait_json, competitiveness FROM student_portrait_4d "
            "WHERE student_id=:sid AND target_job_id <=> :jid",
            {"sid": sid, "jid": jid},
        )
        assert len(after) == 1
        assert after[0]["id"] == rows[0]["id"]
        assert after[0]["competitiveness"] == 62
        after_payload = after[0]["portrait_json"]
        after_payload = json.loads(after_payload) if isinstance(after_payload, str) else after_payload
        assert after_payload["source_portrait_task_id"] == second.json["data"]["task_id"]
        failed_tasks = mysql.fetch_all(
            "SELECT task_id, status FROM pipeline_task WHERE student_id=:sid "
            "AND pipeline='L2_generate_portrait' AND status='failed'",
            {"sid": sid},
        )
        assert len(failed_tasks) == 1
        events = mysql.fetch_all(
            "SELECT status FROM pipeline_task_event WHERE task_id=:task ORDER BY event_id",
            {"task": failed_tasks[0]["task_id"]},
        )
        assert [row["status"] for row in events] == ["pending", "running", "failed"]
    finally:
        mysql.execute("DELETE FROM student_portrait_4d WHERE student_id=:sid", {"sid": sid}, commit=True)
        mysql.execute("DELETE FROM pipeline_task_event WHERE student_id=:sid", {"sid": sid}, commit=True)
        mysql.execute("DELETE FROM pipeline_task WHERE student_id=:sid", {"sid": sid}, commit=True)
        mysql.execute("DELETE FROM student_profile WHERE student_id=:sid", {"sid": sid}, commit=True)
        assert mysql.fetch_all(
            "SELECT COUNT(*) AS total FROM student_portrait_4d WHERE student_id=:sid", {"sid": sid}
        )[0]["total"] == 0
        assert mysql.fetch_all(
            "SELECT COUNT(*) AS total FROM pipeline_task WHERE student_id=:sid", {"sid": sid}
        )[0]["total"] == 0
        assert mysql.fetch_all(
            "SELECT COUNT(*) AS total FROM pipeline_task_event WHERE student_id=:sid", {"sid": sid}
        )[0]["total"] == 0
        assert mysql.fetch_all(
            "SELECT COUNT(*) AS total FROM student_profile WHERE student_id=:sid", {"sid": sid}
        )[0]["total"] == 0
