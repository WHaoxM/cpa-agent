"""Self-reported technical skills are owner-bound exploration inputs, never scores."""

import json
import os
import threading
import uuid

from flask import Flask, g
import pytest
import requests
from sqlalchemy import text
from werkzeug.serving import make_server


@pytest.mark.parametrize("invalid", ["Python", [""], ["X" * 65], ["Vue\n伪造"], [1], ["x"] * 13])
def test_skill_exploration_rejects_invalid_skill_names(invalid):
    from app.services.student_skill_exploration import validated_skills
    with pytest.raises(ValueError):
        validated_skills(invalid)


def test_skill_exploration_deduplicates_and_rejects_cross_resume_report():
    from app.services.student_skill_exploration import current_report, validated_skills
    assert validated_skills([" Vue ", "vue", "TypeScript"]) == ["Vue", "TypeScript"]
    profile = {"_resume_confirmation": {"task_id": "task_current"},
               "_skill_exploration": {"source_confirmation_task_id": "task_old",
                                      "source_kind": "user_self_report", "scoring_eligible": False,
                                      "revision": 1, "skills": ["Vue"]}}
    assert current_report(profile) is None
    profile["_skill_exploration"]["source_confirmation_task_id"] = "task_current"
    profile["_skill_exploration"]["skills"] = [1]
    assert current_report(profile) is None


@pytest.mark.skipif(os.getenv("CPA_TEST_PIPELINE_MYSQL") != "1", reason="requires live MySQL opt-in")
def test_skill_supplement_explores_roles_without_rewriting_resume_or_portrait(monkeypatch):
    from app.api.profile import profile_bp
    from app.services import pipeline, personal_graph_jobs
    from app.services.agent_runtime import resume_confirmation
    from app.services.db import get_mysql_client
    from app.services import career_role_inference
    from app.services.personal_graph import confirmed_projection
    from app.services.resume_diagnostic_report import build_resume_diagnostic_report

    mysql = get_mysql_client()
    if not mysql.test_connection():
        pytest.skip("MySQL unavailable")
    owner = "skill_explore_" + uuid.uuid4().hex[:12]
    other = "skill_explore_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:16]
    candidate = {"name": "探索夹具", "school": "示例大学", "major": "计算机科学",
                 "degree": "本科", "skills": [], "projects": [{"name": "课程项目"}]}
    result = {"task_id": task_id, "student_id": owner, "status": "needs_input",
              "candidate_profile": candidate, "structure_validation": {"ok": True},
              "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": []}}
    monkeypatch.setattr(resume_confirmation, "run_confirmation", lambda **kwargs:
                        pipeline.confirm_resume_candidate(mysql, student_id=kwargs["student_id"],
                                                          task_id=kwargs["task_id"]))
    monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                        lambda *_args, **_kwargs: {"status": "not_requested"})
    inferred = []

    def infer(skills, *, top_k=5, preferred_role=None, **_kwargs):
        inferred.append({"skills": skills, "preferred_role": preferred_role})
        return {"status": "partial", "predicted_role": "前端开发", "confidence": 0.5,
                "candidates": [{"role_name": "前端开发", "matched_skills": ["Vue"]}],
                "skill_count": len(skills)}

    monkeypatch.setattr(career_role_inference, "infer_roles_from_skills", infer)
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    identity = {"principal_id": owner, "student_id": owner}

    @app.before_request
    def principal():
        g.cpa_principal = identity

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    session = requests.Session()
    session.trust_env = False
    try:
        mysql.execute("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
                      "VALUES (:tid,:sid,'L0_parse_resume','needs_input',:result)",
                      {"tid": task_id, "sid": owner, "result": json.dumps(result, ensure_ascii=False)},
                      commit=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        assert session.post(f"{base}/api/profile/student/confirm", json={
            "task_id": task_id, "confirmed": True}, timeout=20).status_code == 200
        before = session.post(f"{base}/api/profile/student/pipeline", json={
            "pipeline": "L2_generate_portrait"}, timeout=30)
        assert before.status_code == 200, before.text
        before_data = before.json()["data"]
        assert before_data["portrait_candidate"]["D2_skill"]["dimension_score"] is None
        baseline_graph = confirmed_projection(mysql, owner)

        saved = session.post(f"{base}/api/profile/student/skill-exploration", json={
            "task_id": task_id, "expected_revision": 0, "skills": ["Vue", "TypeScript"]}, timeout=20)
        assert saved.status_code == 200, saved.text
        supplement = saved.json()["data"]
        assert supplement["revision"] == 1
        assert supplement["source_kind"] == "user_self_report"
        assert supplement["scoring_eligible"] is False
        assert supplement["skills"] == ["Vue", "TypeScript"]
        stored = pipeline.require_confirmed_profile(mysql, owner)
        assert stored.get("skills", []) == []
        assert stored["_skill_exploration"] == supplement
        assert "_skill_exploration" not in result["candidate_profile"]
        assert confirmed_projection(mysql, owner)["snapshot_id"] == baseline_graph["snapshot_id"]
        resume_report = build_resume_diagnostic_report(stored, student_id=owner)
        assert "Vue" not in json.dumps(resume_report, ensure_ascii=False)
        assert "TypeScript" not in json.dumps(resume_report, ensure_ascii=False)

        replay = session.get(f"{base}/api/profile/student/candidate/{task_id}", timeout=20)
        assert replay.status_code == 200, replay.text
        assert replay.json()["data"]["skill_exploration"] == supplement
        assert replay.json()["data"]["portrait_task"]["task_id"] == before_data["task_id"]
        assert replay.json()["data"]["portrait_task"]["portrait_candidate"][
            "D2_skill"]["dimension_score"] is None
        assert mysql.fetch_one("SELECT id FROM student_portrait_4d WHERE student_id=:sid",
                               {"sid": owner}) is None

        roles = session.post(f"{base}/api/profile/student/skill-exploration/roles", json={
            "task_id": task_id, "preferred_role": "前端开发", "skills": ["伪造技能"]}, timeout=20)
        assert roles.status_code == 400, roles.text
        assert not inferred
        roles = session.post(f"{base}/api/profile/student/skill-exploration/roles", json={
            "task_id": task_id, "preferred_role": "前端开发"}, timeout=20)
        assert roles.status_code == 200, roles.text
        assert inferred == [{"skills": ["Vue", "TypeScript"], "preferred_role": "前端开发"}]
        assert roles.json()["data"]["source_kind"] == "user_self_report"
        assert roles.json()["data"]["scoring_eligible"] is False
        assert roles.json()["data"]["roles"]["predicted_role"] == "前端开发"
        rescored = session.post(f"{base}/api/profile/student/pipeline", json={
            "pipeline": "L2_generate_portrait"}, timeout=30)
        assert rescored.status_code == 200, rescored.text
        assert rescored.json()["data"]["portrait_candidate"]["D2_skill"]["dimension_score"] is None
        assert rescored.json()["data"]["portrait_candidate"]["summary"]["competitiveness_score"] is None

        stale = session.post(f"{base}/api/profile/student/skill-exploration", json={
            "task_id": task_id, "expected_revision": 0, "skills": ["Python"]}, timeout=20)
        assert stale.status_code == 409
        identity.update({"principal_id": other, "student_id": other})
        assert session.post(f"{base}/api/profile/student/skill-exploration", json={
            "task_id": task_id, "expected_revision": 0, "skills": ["Python"]}, timeout=20).status_code in (404, 409)
        assert session.post(f"{base}/api/profile/student/skill-exploration/roles", json={
            "task_id": task_id}, timeout=20).status_code in (404, 409)
        identity.update({"principal_id": owner, "student_id": owner})
        withdrawn = session.post(f"{base}/api/profile/student/skill-exploration", json={
            "task_id": task_id, "expected_revision": 1, "skills": []}, timeout=20)
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["data"]["skills"] == []
        assert session.get(f"{base}/api/profile/student/candidate/{task_id}", timeout=20
                           ).json()["data"]["portrait_task"]["task_id"] == rescored.json()["data"]["task_id"]
        replacement_id = "task_" + uuid.uuid4().hex[:16]
        replacement = {**result, "task_id": replacement_id}
        mysql.execute("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
                      "VALUES (:tid,:sid,'L0_parse_resume','needs_input',:result)",
                      {"tid": replacement_id, "sid": owner,
                       "result": json.dumps(replacement, ensure_ascii=False)}, commit=True)
        assert session.post(f"{base}/api/profile/student/confirm", json={
            "task_id": replacement_id, "confirmed": True}, timeout=20).status_code == 200
        assert session.get(f"{base}/api/profile/student/candidate/{task_id}", timeout=20).status_code == 409
        current = session.get(f"{base}/api/profile/student/candidate/{replacement_id}", timeout=20)
        assert current.status_code == 200, current.text
        assert current.json()["data"]["skill_exploration"] is None
        assert session.post(f"{base}/api/profile/student/skill-exploration/roles", json={
            "task_id": task_id}, timeout=20).status_code == 409
    finally:
        server.shutdown()
        if thread.is_alive():
            thread.join(timeout=5)
        with mysql.engine.begin() as conn:
            for table in ("pipeline_task_event", "student_portrait_4d", "pipeline_task", "student_profile"):
                conn.execute(text(f"DELETE FROM {table} WHERE student_id=:sid"), {"sid": owner})
