"""Current workbench read model keeps a partial portrait useful but unscored."""

import json
import os
import threading
import uuid

from flask import Flask, g
import pytest
import requests
from sqlalchemy import text
from werkzeug.serving import make_server


@pytest.mark.skipif(os.getenv("CPA_TEST_PIPELINE_MYSQL") != "1", reason="requires live MySQL opt-in")
def test_workbench_owner_bound_partial_portrait_is_current_result(monkeypatch):
    from app.api.profile import profile_bp
    from app.services import pipeline, personal_graph_jobs, pipeline_portrait_jobs, resume_diagnostic_jobs
    from app.services.agent_runtime import resume_confirmation
    from app.services.db import get_mysql_client

    mysql = get_mysql_client()
    if not mysql.test_connection():
        pytest.skip("MySQL unavailable")
    owner = "workbench_" + uuid.uuid4().hex[:12]
    other = "workbench_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:16]
    candidate = {"name": "虚构学生", "school": "示例大学", "major": "计算机科学",
                 "degree": "本科", "skills": ["Python"],
                 "projects": [{"name": "课程项目", "desc": "编写课程查询接口"}]}
    result = {"task_id": task_id, "student_id": owner, "status": "needs_input",
              "candidate_profile": candidate, "structure_validation": {"ok": True},
              "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": []}}
    monkeypatch.setattr(resume_confirmation, "run_confirmation", lambda **kwargs:
                        pipeline.confirm_resume_candidate(mysql, student_id=kwargs["student_id"],
                                                          task_id=kwargs["task_id"]))
    monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                        lambda *_args, **_kwargs: {"status": "not_requested"})
    real_enqueue_portrait = pipeline_portrait_jobs.enqueue_portrait_intent
    real_enqueue_diagnostic = resume_diagnostic_jobs.enqueue_diagnostic_intent
    monkeypatch.setattr(pipeline_portrait_jobs, "enqueue_portrait_intent",
                        lambda *_args, **_kwargs: "retry_pending")
    monkeypatch.setattr(resume_diagnostic_jobs, "enqueue_diagnostic_intent",
                        lambda *_args, **_kwargs: "retry_pending")
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    identity = {"principal_id": owner, "student_id": owner}

    @app.before_request
    def principal():
        g.cpa_principal = identity.copy()

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
        before = session.get(f"{base}/api/profile/student/workbench", timeout=15)
        assert before.status_code == 200, before.text
        assert before.json()["data"]["run"] == {
            "state": "waiting_for_user", "task_id": task_id, "l0_status": "needs_input"}
        assert before.json()["data"]["portrait"]["state"] == "not_started"
        confirmed = session.post(f"{base}/api/profile/student/confirm",
                                 json={"task_id": task_id, "confirmed": True}, timeout=20)
        assert confirmed.status_code == 200, confirmed.text
        after_confirmation = session.get(f"{base}/api/profile/student/workbench", timeout=15)
        assert after_confirmation.status_code == 200
        assert after_confirmation.json()["data"]["portrait"]["state"] == "processing"
        assert after_confirmation.json()["data"]["portrait"]["task_id"] == (
            confirmed.json()["data"]["portrait_analysis"]["task_id"])
        assert after_confirmation.json()["data"]["resume_diagnostic"]["state"] == "processing"
        diagnostic_task_id = confirmed.json()["data"]["resume_diagnostic_analysis"]["task_id"]
        assert after_confirmation.json()["data"]["resume_diagnostic"]["task_id"] == diagnostic_task_id
        assert after_confirmation.json()["data"]["analysis_run"]["state"] == "running"
        auto_task_id = confirmed.json()["data"]["portrait_analysis"]["task_id"]
        # A lost broker publish leaves both SQL intents intact. Beat's replay
        # can publish them later without another user request or page open.
        with monkeypatch.context() as delivery:
            delivery.setattr(pipeline_portrait_jobs, "enqueue_portrait_intent", real_enqueue_portrait)
            delivery.setattr(resume_diagnostic_jobs, "enqueue_diagnostic_intent", real_enqueue_diagnostic)
            def broker_down(*_args, **_kwargs):
                raise ConnectionError("controlled broker outage")
            delivery.setattr(pipeline_portrait_jobs.portrait_task, "apply_async", broker_down)
            delivery.setattr(resume_diagnostic_jobs.diagnostic_task, "apply_async", broker_down)
            assert real_enqueue_portrait(mysql, auto_task_id) == "retry_pending"
            assert real_enqueue_diagnostic(mysql, diagnostic_task_id) == "retry_pending"
            assert mysql.fetch_one("SELECT status FROM pipeline_task WHERE task_id=:tid",
                                   {"tid": auto_task_id})["status"] == "pending"
            published = []
            delivery.setattr(pipeline_portrait_jobs.portrait_task, "apply_async",
                             lambda **kwargs: published.append(kwargs))
            delivery.setattr(resume_diagnostic_jobs.diagnostic_task, "apply_async",
                             lambda **kwargs: published.append(kwargs))
            assert pipeline_portrait_jobs.replay_portrait_intents(mysql, force=True) == [
                {"task_id": auto_task_id, "delivery": "queued"}]
            assert resume_diagnostic_jobs.replay_diagnostic_intents(mysql, force=True) == [
                {"task_id": diagnostic_task_id, "delivery": "queued"}]
            assert {item["args"][0] for item in published} == {auto_task_id, diagnostic_task_id}
        # No browser pipeline POST: a worker on another process can resume
        # from the committed task ID and owner-bound source in MySQL alone.
        worker_result = pipeline_portrait_jobs.execute_portrait_intent(auto_task_id, mysql=mysql)
        assert worker_result["status"] == "partial", worker_result
        auto_state = session.get(f"{base}/api/profile/student/workbench", timeout=15)
        assert auto_state.status_code == 200
        assert auto_state.json()["data"]["portrait"]["state"] == "ready_with_unknowns"
        assert auto_state.json()["data"]["portrait"]["task_id"] == auto_task_id
        first_snapshot_id = auto_state.json()["data"]["portrait"]["snapshot_id"]
        assert first_snapshot_id.startswith("ps_")
        initial_history = session.get(f"{base}/api/profile/student/portrait-snapshots", timeout=15)
        assert initial_history.status_code == 200, initial_history.text
        assert initial_history.json()["data"]["current_snapshot_id"] == first_snapshot_id
        assert len(initial_history.json()["data"]["items"]) == 1
        duplicate = pipeline_portrait_jobs.execute_portrait_intent(auto_task_id, mysql=mysql)
        assert duplicate["status"] == "already_claimed_or_terminal"
        class UnavailableModel:
            def chat_with_metadata(self, **_kwargs):
                raise RuntimeError("controlled model outage")
        diagnostic_result = resume_diagnostic_jobs.execute_diagnostic_intent(
            diagnostic_task_id, mysql=mysql, llm_client=UnavailableModel())
        assert diagnostic_result["status"] == "partial", diagnostic_result
        assert session.get(f"{base}/api/profile/student/workbench", timeout=15
                           ).json()["data"]["resume_diagnostic"]["state"] == "partial"
        assert resume_diagnostic_jobs.execute_diagnostic_intent(
            diagnostic_task_id, mysql=mysql)["status"] == "already_claimed_or_terminal"
        generated = session.post(f"{base}/api/profile/student/pipeline",
                                 json={"pipeline": "L2_generate_portrait"}, timeout=40)
        assert generated.status_code == 200, generated.text
        assert generated.json()["data"]["status"] == "partial"
        response = session.get(f"{base}/api/profile/student/workbench", timeout=15)
        assert response.status_code == 200, response.text
        state = response.json()["data"]
        assert state["source_confirmation_task_id"] == task_id
        assert state["run"]["state"] == "settled"
        assert state["resume_diagnostic"]["state"] == "partial"
        assert state["analysis_run"]["state"] == "settled"
        assert state["analysis_run"]["has_failures"] is False
        portrait = state["portrait"]
        assert portrait["state"] == "ready_with_unknowns"
        assert portrait["scoring_eligible"] is False
        assert portrait["portrait_id"] is None
        assert portrait["snapshot_id"] is not None
        assert portrait["snapshot_id"] != first_snapshot_id
        history = session.get(f"{base}/api/profile/student/portrait-snapshots", timeout=15)
        assert history.status_code == 200, history.text
        assert len(history.json()["data"]["items"]) == 2
        old = session.get(f"{base}/api/profile/student/portrait-snapshots/{first_snapshot_id}",
                          timeout=15)
        assert old.status_code == 200, old.text
        assert old.json()["data"]["is_current"] is False
        assert old.json()["data"]["scoring_eligible"] is False
        assert old.json()["data"]["portrait"]["source_confirmation_task_id"] == task_id
        assert portrait["dimensions"]["D2"] is not None
        assert portrait["dimensions"]["D1"] is None
        assert portrait["dimensions"]["D4"] is None
        assert "school_tier" in portrait["missing_fields"]
        assert mysql.fetch_all("SELECT id FROM student_portrait_4d WHERE student_id=:sid", {"sid": owner}) == []
        newer_task_id = "task_" + uuid.uuid4().hex[:16]
        mysql.execute("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
                      "VALUES (:tid,:sid,'L0_parse_resume','pending',:result)",
                      {"tid": newer_task_id, "sid": owner,
                       "result": json.dumps({"task_id": newer_task_id, "student_id": owner,
                                             "status": "pending"})}, commit=True)
        during_new_upload = session.get(f"{base}/api/profile/student/workbench", timeout=15)
        assert during_new_upload.status_code == 200
        new_state = during_new_upload.json()["data"]
        assert new_state["run"]["state"] == "queued"
        assert new_state["based_on_previous_upload"] is True
        assert new_state["source_confirmation_task_id"] == task_id
        assert new_state["portrait"]["task_id"] == portrait["task_id"]
        identity.update(principal_id=other, student_id=other)
        stranger = session.get(f"{base}/api/profile/student/workbench", timeout=15)
        assert stranger.status_code == 200
        assert stranger.json()["data"]["source_confirmation_task_id"] is None
        assert stranger.json()["data"]["portrait"]["state"] == "not_started"
        stranger_history = session.get(
            f"{base}/api/profile/student/portrait-snapshots/{first_snapshot_id}", timeout=15)
        assert stranger_history.status_code == 404
        identity.update(principal_id=owner, student_id=owner)
        original_result = mysql.fetch_one(
            "SELECT result_json FROM pipeline_task WHERE task_id=:tid",
            {"tid": portrait["task_id"]})["result_json"]
        mysql.execute("UPDATE pipeline_task SET result_json=JSON_SET(result_json, '$.resource_id', 'task_wrong') "
                      "WHERE task_id=:tid", {"tid": portrait["task_id"]}, commit=True)
        forged = session.get(f"{base}/api/profile/student/workbench", timeout=15)
        assert forged.status_code == 409
        assert forged.json()["success"] is False
        mysql.execute("UPDATE pipeline_task SET result_json=:result WHERE task_id=:tid",
                      {"result": json.dumps(original_result, ensure_ascii=False)
                       if isinstance(original_result, dict) else original_result,
                       "tid": portrait["task_id"]}, commit=True)
        # Confirm a newer resume. The old version remains owner-readable, but
        # must immediately lose current/scoring status while the new run waits.
        newer_result = {**result, "task_id": newer_task_id, "status": "needs_input"}
        mysql.execute("UPDATE pipeline_task SET status='needs_input',result_json=:result "
                      "WHERE task_id=:tid AND student_id=:sid",
                      {"tid": newer_task_id, "sid": owner,
                       "result": json.dumps(newer_result, ensure_ascii=False)}, commit=True)
        next_confirmed = session.post(f"{base}/api/profile/student/confirm",
                                      json={"task_id": newer_task_id, "confirmed": True}, timeout=20)
        assert next_confirmed.status_code == 200, next_confirmed.text
        newer_state = session.get(f"{base}/api/profile/student/workbench", timeout=15).json()["data"]
        assert newer_state["source_confirmation_task_id"] == newer_task_id
        assert newer_state["portrait"]["state"] == "processing"
        old_after_new_resume = session.get(
            f"{base}/api/profile/student/portrait-snapshots/{first_snapshot_id}", timeout=15)
        assert old_after_new_resume.status_code == 200, old_after_new_resume.text
        assert old_after_new_resume.json()["data"]["is_current"] is False
        assert old_after_new_resume.json()["data"]["scoring_eligible"] is False
        assert session.get(f"{base}/api/profile/student/candidate/{task_id}", timeout=15).status_code == 409
        new_task_id = next_confirmed.json()["data"]["portrait_analysis"]["task_id"]
        assert pipeline_portrait_jobs.execute_portrait_intent(new_task_id, mysql=mysql)["status"] == "partial"
        new_current = session.get(f"{base}/api/profile/student/workbench", timeout=15).json()["data"]
        assert new_current["portrait"]["snapshot_id"] not in {portrait["snapshot_id"], first_snapshot_id}
        assert len(session.get(f"{base}/api/profile/student/portrait-snapshots", timeout=15
                               ).json()["data"]["items"]) == 3
        mysql.execute("UPDATE student_portrait_snapshot SET portrait_json="
                      "JSON_SET(portrait_json,'$.D2_skill.dimension_score',0) "
                      "WHERE snapshot_id=:snapshot_id AND student_id=:sid",
                      {"snapshot_id": first_snapshot_id, "sid": owner}, commit=True)
        tampered_history = session.get(
            f"{base}/api/profile/student/portrait-snapshots/{first_snapshot_id}", timeout=15)
        assert tampered_history.status_code == 409
    finally:
        server.shutdown()
        thread.join(timeout=3)
        session.close()
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_portrait_dispatch WHERE task_id IN "
                              "(SELECT task_id FROM pipeline_task WHERE student_id=:sid)"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task_event WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_portrait_4d WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM resume_diagnostic_report WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})
