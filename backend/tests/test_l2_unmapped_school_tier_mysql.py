"""Opt-in HTTP/MySQL regression for a quoted but unscorable school category.

Run with CPA_TEST_LIVE_MYSQL=1. The fixture is deliberately synthetic and is
removed after the test; it never substitutes a valid school tier to get a score.
"""

import json
import os
import uuid

import pytest
from flask import g

from app import create_app
from app.api._helpers import Principal
from app.config import Config
from app.pipeline.entity_extractor.resume_structure import _ground_semantic_facts
from app.services.db.mysql_client import get_mysql_client
from app.services.pipeline import confirm_resume_candidate


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_confirmed_wrong_school_category_remains_readable_and_l2_does_not_persist_score(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    sid = "l2_tier_" + uuid.uuid4().hex[:16]
    l0 = "task_" + uuid.uuid4().hex[:20]
    source = "学校层级其他理工；专业计算机科学与技术；沟通强；团队强；抗压强；负责强"
    candidate = _ground_semantic_facts({
        "school": "某大学", "major": "计算机科学与技术", "degree": "本科",
        "projects": [{"name": "项目"}], "skills": ["Python"],
    }, {"semantic_facts": {
        "school_tier": {"value": "其他理工", "evidence": "其他理工"},
        "major_category": {"value": "计算机科学与技术", "evidence": "计算机科学与技术"},
        "communication": {"value": "强", "evidence": "沟通强"},
        "teamwork": {"value": "强", "evidence": "团队强"},
        "stress_tolerance": {"value": "强", "evidence": "抗压强"},
        "responsibility": {"value": "强", "evidence": "负责强"},
    }}, source)
    assert candidate["semantic_fact_next_action"] == "review_unmapped_scoring_category"
    mysql = get_mysql_client()
    try:
        mysql.execute(
            "INSERT INTO pipeline_task(task_id,pipeline,status,student_id,params_json,result_json) "
            "VALUES (:task,'L0_parse_resume','needs_input',:sid,'{}',:result)",
            {"task": l0, "sid": sid, "result": json.dumps({
                "task_id": l0, "student_id": sid, "status": "needs_input", "confirmed": False,
                "candidate_profile": candidate, "structure_validation": {"ok": True},
                "source_evidence": {"missing_pages": [], "blocks": [{
                    "block_id": "b1", "page": 1, "text": source,
                }]},
            }, ensure_ascii=False)}, commit=True,
        )
        from app.services import personal_graph_jobs
        monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                            lambda *_args, **_kwargs: {"status": "not_requested"})
        confirmed_result = confirm_resume_candidate(mysql, student_id=sid, task_id=l0)
        assert confirmed_result["status"] == "completed"
        assert confirmed_result["portrait_ready"] is False
        app = create_app()

        @app.before_request
        def bind_principal():
            g.cpa_principal = Principal(sid, sid, "live-test")

        client = app.test_client()
        confirmed = client.get(f"/api/profile/student/candidate/{l0}")
        assert confirmed.status_code == 200, confirmed.json
        assert confirmed.json["data"]["confirmed"] is True
        assert confirmed.json["data"]["candidate_profile"]["semantic_facts"]["school_tier"]["value"] == "其他理工"
        assert confirmed.json["data"]["candidate_profile"]["semantic_fact_next_action"] == "review_unmapped_scoring_category"

        response = client.post("/api/profile/student/pipeline", json={"pipeline": "L2_generate_portrait"})
        assert response.status_code == 200, response.json
        result = response.json["data"]
        assert result["status"] == response.json["status"] == "partial"
        assert result["portrait_id"] is None
        assert result["portrait_candidate"]["D1_basic"]["school_tier"] == "其他理工"
        assert result["portrait_candidate"]["D1_basic"]["sub_school"] is None
        assert result["portrait_candidate"]["D3_quality"]["dimension_score"] is not None
        assert result["portrait_candidate"]["summary"]["competitiveness_score"] is None
        assert result["missing_fields"] == ["school_tier"]
        assert result["action_available"] is False
        task_rows = mysql.fetch_all(
            "SELECT status, result_json FROM pipeline_task WHERE task_id=:task AND student_id=:sid",
            {"task": result["task_id"], "sid": sid},
        )
        assert len(task_rows) == 1 and task_rows[0]["status"] == "partial"
        stored = task_rows[0]["result_json"]
        stored = json.loads(stored) if isinstance(stored, str) else stored
        assert stored["portrait_id"] is None
        assert mysql.fetch_all(
            "SELECT id FROM student_portrait_4d WHERE student_id=:sid", {"sid": sid},
        ) == []
    finally:
        mysql.execute("DELETE FROM student_portrait_4d WHERE student_id=:sid", {"sid": sid}, commit=True)
        mysql.execute("DELETE FROM pipeline_task_event WHERE student_id=:sid", {"sid": sid}, commit=True)
        mysql.execute("DELETE FROM pipeline_task WHERE student_id=:sid", {"sid": sid}, commit=True)
        mysql.execute("DELETE FROM student_profile WHERE student_id=:sid", {"sid": sid}, commit=True)
        for table in ("student_portrait_4d", "pipeline_task_event", "pipeline_task", "student_profile"):
            assert mysql.fetch_all(f"SELECT COUNT(*) AS total FROM {table} WHERE student_id=:sid",
                                   {"sid": sid})[0]["total"] == 0
