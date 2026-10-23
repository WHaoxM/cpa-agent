"""Current workbench read model keeps a partial portrait useful but unscored."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from flask import Flask, g
import pytest
import requests
from sqlalchemy import text
from werkzeug.serving import make_server


@pytest.fixture
def workbench_isolated_mysql(monkeypatch):
    """Create a disposable schema for workbench integration scenarios only."""
    if os.getenv("CPA_TEST_LIVE_MYSQL") != "1":
        pytest.skip("Set CPA_TEST_LIVE_MYSQL=1 for disposable workbench MySQL tests")

    import pymysql
    from app.config import Config
    from app.services.db import mysql_client as mysql_module
    from app.services.db.mysql_client import MySQLClient

    backend = Path(__file__).resolve().parents[1]
    database = f"cpa_workbench_scenario_{uuid.uuid4().hex[:12]}"
    admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            autocommit=True)
    mysql = None
    database_created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        database_created = True
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=backend, check=True, capture_output=True, text=True,
        )
        mysql = MySQLClient(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            database=database, pool_size=2, max_overflow=0)
        monkeypatch.setattr(mysql_module, "_mysql_client", mysql)
        monkeypatch.setattr(Config, "MYSQL_DATABASE", database)
        yield mysql, database
    finally:
        if mysql is not None:
            mysql.close()
        try:
            if database_created:
                with admin.cursor() as cursor:
                    cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        finally:
            admin.close()


def _seed_confirmed_resume(mysql, *, student_id, confirmation_task_id, candidate):
    """Persist the same owner-bound source shape produced by resume confirmation."""
    confirmed_profile = {
        **candidate,
        "_resume_confirmation": {
            "task_id": confirmation_task_id,
            "student_id": student_id,
            "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": []},
        },
    }
    with mysql.engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO student_profile(student_id,profile_json) VALUES (:sid,:profile)"),
            {"sid": student_id, "profile": json.dumps(confirmed_profile, ensure_ascii=False)})
        conn.execute(text(
            "INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
            "VALUES (:tid,:sid,'L0_parse_resume','completed',:result)"),
            {"tid": confirmation_task_id, "sid": student_id,
             "result": json.dumps({"confirmed": True, "candidate_profile": candidate},
                                  ensure_ascii=False)})
    return confirmed_profile


def _start_profile_http(identity):
    """Serve the real profile routes over loopback with a controlled Principal."""
    from app.api.profile import profile_bp
    from app.api.report import report_bp

    app = Flask(f"workbench-scenario-{uuid.uuid4().hex}")
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    app.register_blueprint(report_bp, url_prefix="/api/report")

    @app.before_request
    def attach_principal():
        g.cpa_principal = identity.copy()

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="requires disposable MySQL opt-in")
def test_workbench_owner_bound_partial_portrait_is_current_result(monkeypatch, workbench_isolated_mysql):
    from app.api.profile import profile_bp
    from app.services import pipeline, personal_graph_jobs, pipeline_portrait_jobs, resume_diagnostic_jobs
    from app.services.agent_runtime import resume_confirmation
    from app.services.db.mysql_client import get_mysql_client

    mysql, _database = workbench_isolated_mysql
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


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="requires disposable MySQL opt-in")
def test_workbench_cross_owner_candidate_and_confirmation_have_no_effect(workbench_isolated_mysql):
    """A second student cannot inspect or confirm another student's candidate."""
    mysql, _database = workbench_isolated_mysql
    owner = "workbench_owner_" + uuid.uuid4().hex[:12]
    stranger = "workbench_stranger_" + uuid.uuid4().hex[:12]
    task_id = "task_workbench_" + uuid.uuid4().hex[:16]
    candidate = {"name": "合成学生", "school": "示例大学", "skills": ["Python"]}
    result = {"task_id": task_id, "student_id": owner, "status": "needs_input",
              "candidate_profile": candidate, "structure_validation": {"ok": True},
              "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": []}}
    mysql.execute(
        "INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
        "VALUES (:tid,:sid,'L0_parse_resume','needs_input',:result)",
        {"tid": task_id, "sid": owner, "result": json.dumps(result, ensure_ascii=False)}, commit=True)
    identity = {"principal_id": stranger, "student_id": stranger}
    server, thread, base = _start_profile_http(identity)
    session = requests.Session()
    session.trust_env = False
    try:
        hidden_candidate = session.get(f"{base}/api/profile/student/candidate/{task_id}", timeout=10)
        assert hidden_candidate.status_code == 404
        rejected_confirmation = session.post(
            f"{base}/api/profile/student/confirm",
            json={"task_id": task_id, "confirmed": True}, timeout=10,
        )
        assert rejected_confirmation.status_code == 404
        stranger_workbench = session.get(f"{base}/api/profile/student/workbench", timeout=10)
        assert stranger_workbench.status_code == 200
        assert stranger_workbench.json()["data"]["source_confirmation_task_id"] is None

        stored = mysql.fetch_one(
            "SELECT student_id,status,result_json FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
        assert stored["student_id"] == owner
        assert stored["status"] == "needs_input"
        assert mysql.fetch_one(
            "SELECT COUNT(*) AS n FROM pipeline_task WHERE student_id=:sid "
            "AND pipeline IN ('L2_generate_portrait','R1_resume_diagnostic')",
            {"sid": owner})["n"] == 0
        assert mysql.fetch_one(
            "SELECT student_id FROM student_profile WHERE student_id=:sid", {"sid": stranger}) is None

        identity.update(principal_id=owner, student_id=owner)
        owner_candidate = session.get(f"{base}/api/profile/student/candidate/{task_id}", timeout=10)
        assert owner_candidate.status_code == 200, owner_candidate.text
        assert owner_candidate.json()["data"]["candidate_profile"] == candidate
    finally:
        session.close()
        server.shutdown()
        thread.join(timeout=3)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="requires disposable MySQL opt-in")
def test_workbench_diagnostic_model_outage_is_visible_as_partial_not_completed(
    monkeypatch, workbench_isolated_mysql,
):
    """A model outage leaves a readable partial report, not a false success."""
    from app.services import personal_graph_jobs, pipeline, resume_diagnostic_jobs, pipeline_portrait_jobs
    from app.services.agent_runtime import resume_confirmation

    mysql, _database = workbench_isolated_mysql
    owner = "workbench_model_" + uuid.uuid4().hex[:12]
    task_id = "task_workbench_" + uuid.uuid4().hex[:16]
    candidate = {"name": "合成学生", "school": "示例大学", "major": "计算机科学",
                 "degree": "本科", "skills": ["Python"],
                 "projects": [{"name": "课程项目", "desc": "实现合成项目"}]}
    result = {"task_id": task_id, "student_id": owner, "status": "needs_input",
              "candidate_profile": candidate, "structure_validation": {"ok": True},
              "source_evidence": {"page_count": 1, "observed_pages": [1], "missing_pages": []}}
    mysql.execute(
        "INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
        "VALUES (:tid,:sid,'L0_parse_resume','needs_input',:result)",
        {"tid": task_id, "sid": owner, "result": json.dumps(result, ensure_ascii=False)}, commit=True)
    monkeypatch.setattr(resume_confirmation, "run_confirmation", lambda **kwargs:
                        pipeline.confirm_resume_candidate(mysql, student_id=kwargs["student_id"],
                                                          task_id=kwargs["task_id"]))
    monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                        lambda *_args, **_kwargs: {"status": "not_requested"})
    monkeypatch.setattr(pipeline_portrait_jobs, "enqueue_portrait_intent",
                        lambda *_args, **_kwargs: "retry_pending")
    monkeypatch.setattr(resume_diagnostic_jobs, "enqueue_diagnostic_intent",
                        lambda *_args, **_kwargs: "retry_pending")

    identity = {"principal_id": owner, "student_id": owner}
    server, thread, base = _start_profile_http(identity)
    session = requests.Session()
    session.trust_env = False

    class UnavailableModel:
        def chat_with_metadata(self, **_kwargs):
            raise RuntimeError("controlled model outage")

    try:
        confirmed = session.post(
            f"{base}/api/profile/student/confirm",
            json={"task_id": task_id, "confirmed": True}, timeout=20,
        )
        assert confirmed.status_code == 200, confirmed.text
        diagnostic_id = confirmed.json()["data"]["resume_diagnostic_analysis"]["task_id"]
        diagnostic = resume_diagnostic_jobs.execute_diagnostic_intent(
            diagnostic_id, mysql=mysql, llm_client=UnavailableModel())
        assert diagnostic["status"] == "partial"
        assert diagnostic["missing_sections"]

        visible_state = session.get(f"{base}/api/profile/student/workbench", timeout=10)
        assert visible_state.status_code == 200, visible_state.text
        assert visible_state.json()["data"]["resume_diagnostic"]["state"] == "partial"
        report_id = diagnostic["report_id"]
        visible_report = session.get(f"{base}/api/report/resume-diagnostic/{report_id}", timeout=10)
        assert visible_report.status_code == 200, visible_report.text
        assert visible_report.json()["data"]["status"] == "partial"
        assert visible_report.json()["data"]["missing_sections"]

        task_row = mysql.fetch_one(
            "SELECT status,result_json FROM pipeline_task WHERE task_id=:tid AND student_id=:sid",
            {"tid": diagnostic_id, "sid": owner})
        assert task_row["status"] == "partial"
        reports = mysql.fetch_all(
            "SELECT report_id,status FROM resume_diagnostic_report "
            "WHERE student_id=:sid AND source_confirmation_task_id=:tid",
            {"sid": owner, "tid": task_id})
        assert reports == [{"report_id": report_id, "status": "partial"}]
        assert resume_diagnostic_jobs.execute_diagnostic_intent(
            diagnostic_id, mysql=mysql, llm_client=UnavailableModel()
        )["status"] == "already_claimed_or_terminal"
    finally:
        session.close()
        server.shutdown()
        thread.join(timeout=3)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_CELERY") != "1"
                    or os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_CELERY=1 and CPA_TEST_LIVE_MYSQL=1")
def test_workbench_real_worker_and_beat_replay_persisted_portrait_intent(
    workbench_isolated_mysql, tmp_path, request,
):
    """Beat replays a committed portrait intent and a real worker persists its result."""
    import redis
    from celery import Celery
    from app.config import Config
    from app.services.pipeline_portrait_jobs import persist_portrait_intent_tx

    mysql, database = workbench_isolated_mysql
    broker = os.getenv(
        "CPA_TEST_WORKBENCH_CELERY_BROKER_URL", "redis://127.0.0.1:16379/15",
    )
    redis_client = redis.Redis.from_url(broker, socket_connect_timeout=2, socket_timeout=2)
    try:
        redis_client.ping()
    except redis.RedisError:
        redis_client.close()
        pytest.skip("dedicated workbench Celery Redis DB unavailable")
    if redis_client.dbsize() != 0:
        redis_client.close()
        pytest.fail("workbench Celery Redis DB must be empty before the test")
    baseline_redis_keys = set(redis_client.scan_iter())

    unique = uuid.uuid4().hex[:12]
    redis_lock = f"cpa-workbench-celery-lock:{unique}"
    if not redis_client.set(redis_lock, unique, nx=True, ex=1800):
        redis_client.close()
        pytest.fail("could not reserve workbench Celery Redis DB")

    def cleanup_redis():
        created_keys = set(redis_client.scan_iter()) - baseline_redis_keys
        if created_keys:
            redis_client.delete(*created_keys)
        redis_client.close()

    request.addfinalizer(cleanup_redis)

    owner = f"workbench_celery_{unique}"
    confirmation_id = f"task_confirm_{unique}"
    queue = f"cpa.workbench.{unique}"
    node = f"cpa-workbench-{unique}@localhost"
    candidate = {"name": "合成学生", "school": "示例大学", "major": "计算机科学",
                 "degree": "本科", "skills": ["Python"],
                 "projects": [{"name": "课程项目", "desc": "实现合成项目"}]}
    _seed_confirmed_resume(mysql, student_id=owner,
                           confirmation_task_id=confirmation_id, candidate=candidate)
    with mysql.engine.begin() as conn:
        portrait_task_id = persist_portrait_intent_tx(
            conn, student_id=owner, confirmation_task_id=confirmation_id)
    persisted = mysql.fetch_one(
        "SELECT t.status,t.student_id,t.pipeline,d.attempts,d.last_enqueued_at "
        "FROM pipeline_task t JOIN pipeline_portrait_dispatch d ON d.task_id=t.task_id "
        "WHERE t.task_id=:tid", {"tid": portrait_task_id})
    assert persisted == {"status": "pending", "student_id": owner,
                         "pipeline": "L2_generate_portrait", "attempts": 0,
                         "last_enqueued_at": None}

    backend = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({
        "CPA_MEDIA_BROKER_URL": broker,
        "CPA_PIPELINE_QUEUE": queue,
        "CPA_AGENT_QUEUE": f"cpa.agent.workbench.{unique}",
        "CPA_PIPELINE_REPLAY_INTERVAL_SECONDS": "5",
        "MYSQL_HOST": str(Config.MYSQL_HOST),
        "MYSQL_PORT": str(Config.MYSQL_PORT),
        "MYSQL_USER": str(Config.MYSQL_USER),
        "MYSQL_PASSWORD": str(Config.MYSQL_PASSWORD),
        "MYSQL_DATABASE": database,
        "CPA_WORKBENCH_CELERY_DATABASE": database,
        "CPA_DEMO_MODE": "false",
        "PYTHONPATH": os.pathsep.join((str(backend / "tests"), str(backend),
                                        env.get("PYTHONPATH", ""))),
        "PYTHONUTF8": "1",
    })
    command = [sys.executable, "-B", "-m", "celery", "-A",
               "workbench_celery_probe:celery_app"]
    worker = beat = None
    worker_log_path = tmp_path / "workbench-worker.log"
    beat_log_path = tmp_path / "workbench-beat.log"
    worker_log = worker_log_path.open("wb")
    beat_log = None
    control_app = Celery("cpa_workbench_control", broker=broker)
    try:
        worker = subprocess.Popen(
            command + ["worker", "--pool=solo", "--concurrency=1", f"--queues={queue}",
                       f"--hostname={node}", "--loglevel=INFO"],
            cwd=backend, env=env, stdout=worker_log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        registered = None
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(f"workbench worker exited early with code {worker.returncode}")
            registered = control_app.control.inspect(destination=[node], timeout=2).registered() or {}
            if node in registered:
                names = {task.split("[")[0] for task in registered[node]}
                assert "cpa.pipeline.portrait" in names
                assert "cpa.pipeline.portrait_replay" in names
                break
            time.sleep(1)
        else:
            pytest.fail("workbench Celery worker did not register the portrait tasks")

        beat_schedule = tmp_path / "workbench-celerybeat-schedule"
        beat_log = beat_log_path.open("wb")
        beat = subprocess.Popen(
            command + ["beat", f"--schedule={beat_schedule}", "--loglevel=INFO"],
            cwd=backend, env=env, stdout=beat_log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        task_row = None
        while time.monotonic() < deadline:
            if beat.poll() is not None:
                pytest.fail(f"workbench Beat exited early with code {beat.returncode}")
            if worker.poll() is not None:
                pytest.fail(f"workbench worker exited with code {worker.returncode}")
            task_row = mysql.fetch_one(
                "SELECT status,result_json FROM pipeline_task WHERE task_id=:tid AND student_id=:sid",
                {"tid": portrait_task_id, "sid": owner})
            if task_row and task_row["status"] in ("partial", "completed", "failed"):
                break
            time.sleep(0.5)
        if not task_row or task_row["status"] not in ("partial", "completed", "failed"):
            worker_log.flush()
            beat_log.flush()
            redis_keys = sorted(
                key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
                for key in redis_client.scan_iter()
            )
            pytest.fail(
                f"portrait intent was not replayed by Beat; task={task_row}; "
                f"queue_len={redis_client.llen(queue)}; redis_keys={redis_keys}; "
                f"worker_log={worker_log_path.read_text(encoding='utf-8', errors='replace')}; "
                f"beat_log={beat_log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        assert task_row["status"] == "partial", task_row
        result = task_row["result_json"]
        result = json.loads(result) if isinstance(result, str) else result
        assert result["status"] == "partial"
        assert result["resource_id"] == portrait_task_id
        assert result["source_confirmation_task_id"] == confirmation_id
        assert result["snapshot_id"].startswith("ps_")
        dispatch = mysql.fetch_one(
            "SELECT attempts,claim_token FROM pipeline_portrait_dispatch WHERE task_id=:tid",
            {"tid": portrait_task_id})
        assert dispatch["attempts"] == 1
        assert dispatch["claim_token"]
        snapshots = mysql.fetch_all(
            "SELECT snapshot_id,student_id,source_task_id,status FROM student_portrait_snapshot "
            "WHERE source_task_id=:tid", {"tid": portrait_task_id})
        assert snapshots == [{"snapshot_id": result["snapshot_id"], "student_id": owner,
                              "source_task_id": portrait_task_id, "status": "partial"}]
        assert mysql.fetch_all(
            "SELECT id FROM student_portrait_4d WHERE student_id=:sid", {"sid": owner}) == []

        # A duplicate real delivery is acknowledged but cannot mint a second result.
        control_app.send_task("cpa.pipeline.portrait", args=[portrait_task_id], queue=queue,
                              task_id=f"duplicate-{unique}")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            inspect = control_app.control.inspect(destination=[node], timeout=1)
            active = inspect.active() or {}
            reserved = inspect.reserved() or {}
            if (redis_client.llen(queue) == 0 and not active.get(node)
                    and not reserved.get(node)):
                break
            time.sleep(0.25)
        assert redis_client.llen(queue) == 0
        assert mysql.fetch_one(
            "SELECT attempts FROM pipeline_portrait_dispatch WHERE task_id=:tid",
            {"tid": portrait_task_id})["attempts"] == 1
        assert mysql.fetch_one(
            "SELECT COUNT(*) AS n FROM student_portrait_snapshot WHERE source_task_id=:tid",
            {"tid": portrait_task_id})["n"] == 1
    finally:
        for process in (beat, worker):
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        if beat_log is not None:
            beat_log.close()
        worker_log.close()


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="requires disposable MySQL schema and dedicated Redis test DB")
def test_authenticated_http_resume_upload_to_workbench_journey(monkeypatch, tmp_path):
    """Exercise the student journey over HTTP against isolated persistent state.

    The parser and diagnostic model are deterministic substitutes. Flask routes,
    account cookies/CSRF, LangGraph confirmation, MySQL transactions and the
    workbench/result read APIs execute normally. Task executors run directly in
    the test process; this does not prove a separate Celery worker process.
    """
    import json
    from pathlib import Path
    import subprocess
    import sys
    import threading
    import uuid
    from types import SimpleNamespace

    import pymysql
    import redis
    import requests
    from werkzeug.serving import make_server

    from app import create_app
    from app.config import Config
    from app.services import pipeline_portrait_jobs, personal_graph_jobs, resume_diagnostic_jobs
    from app.services.account_session_schema import migrate_account_session_schema
    from app.services.db import mysql_client as mysql_module
    from app.services.db.mysql_client import MySQLClient
    from app.services import pipeline_resume_jobs

    backend = Path(__file__).resolve().parents[1]
    nonce = uuid.uuid4().hex[:12]
    database = f"cpa_workbench_{nonce}"
    redis_url = os.getenv("CPA_TEST_WORKBENCH_REDIS_URL", "redis://127.0.0.1:16379/13")
    redis_client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        redis_client.ping()
    except redis.RedisError:
        redis_client.close()
        pytest.skip("dedicated workbench Redis test DB unavailable")
    if redis_client.dbsize() != 0:
        redis_client.close()
        pytest.skip("workbench Redis test DB must be empty before the journey")
    baseline_redis_keys = set(redis_client.scan_iter())
    redis_lock = f"cpa-workbench-test-lock:{nonce}"
    if not redis_client.set(redis_lock, nonce, nx=True, ex=1800):
        redis_client.close()
        pytest.skip("could not reserve workbench Redis test DB")

    admin = None
    mysql = None
    server = thread = None
    student = stranger = None
    database_created = False
    try:
        admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                                user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                                autocommit=True)
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        database_created = True
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=backend, check=True, capture_output=True, text=True,
        )

        mysql = MySQLClient(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            database=database, pool_size=2, max_overflow=0)
        monkeypatch.setattr(mysql_module, "_mysql_client", mysql)
        monkeypatch.setattr(Config, "MYSQL_DATABASE", database)
        monkeypatch.setattr(Config, "DEMO_MODE", False)
        monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
        migrate_account_session_schema(mysql)

        auth_config = type("WorkbenchJourneyConfig", (Config,), {
            "SECRET_KEY": "workbench-http-test-secret-1234567890-abcdef",
            "DEMO_MODE": False,
            "CPA_AUTH_COOKIE_SECURE": False,
            "REDIS_URL": redis_url,
            "UPLOAD_FOLDER": str(tmp_path),
            "RATELIMIT_KEY_PREFIX": f"workbench-journey-{nonce}",
            "CORS_ALLOWED_ORIGINS": [],
        })
        # The account adapter also checks the imported Config class, not just
        # app.config; both are pinned to strict mode for this fixture.
        app = create_app(auth_config)
        assert app.extensions.get("cpa_account_auth_enabled") is True
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        student, stranger = requests.Session(), requests.Session()
        student.trust_env = stranger.trust_env = False

        anonymous = requests.Session()
        anonymous.trust_env = False
        assert anonymous.get(f"{base}/api/profile/student/workbench", timeout=5).status_code == 401
        anonymous_upload = anonymous.post(
            f"{base}/api/profile/student/upload?async=1",
            files={"file": ("anonymous.txt", b"synthetic", "text/plain")}, timeout=5,
        )
        assert anonymous_upload.status_code == 401
        anonymous.close()

        password = "workbench-test-password-42"
        owner_name, stranger_name = f"wb_{nonce}", f"wb_other_{nonce}"

        def register_and_login(client, username):
            csrf_response = client.get(f"{base}/api/auth/csrf", timeout=5)
            assert csrf_response.status_code == 200, csrf_response.text
            csrf = csrf_response.json()["csrfToken"]
            credentials = {"username": username, "password": password}
            registered = client.post(f"{base}/api/auth/register", json=credentials,
                                     headers={"X-CSRFToken": csrf}, timeout=10)
            assert registered.status_code == 201, registered.text
            account_id = registered.json()["user"]["id"]
            logged_in = client.post(f"{base}/api/auth/login", json=credentials,
                                    headers={"X-CSRFToken": csrf}, timeout=10)
            assert logged_in.status_code == 200, logged_in.text
            assert client.get(f"{base}/api/auth/me", timeout=5).json()["user"]["id"] == account_id
            return account_id, csrf

        owner, csrf = register_and_login(student, owner_name)
        stranger_id, _ = register_and_login(stranger, stranger_name)
        assert owner != stranger_id

        invalid_upload = student.post(
            f"{base}/api/profile/student/upload?async=1",
            files={"file": ("resume.exe", b"synthetic", "application/octet-stream")},
            headers={"X-CSRFToken": csrf}, timeout=5,
        )
        assert invalid_upload.status_code == 400
        assert mysql.fetch_one("SELECT task_id FROM pipeline_task WHERE student_id=:sid LIMIT 1",
                               {"sid": owner}) is None
        assert not list(Path(tmp_path).glob(f"{owner}/**/*"))

        # Do not publish these test jobs to a developer worker. The durable
        # MySQL intents are written by the real route and then claimed by the
        # same real task executors with deterministic parser/model adapters.
        monkeypatch.setattr(pipeline_resume_jobs, "enqueue_resume_intent",
                            lambda *_args, **_kwargs: "retry_pending")
        monkeypatch.setattr(pipeline_portrait_jobs, "enqueue_portrait_intent",
                            lambda *_args, **_kwargs: "retry_pending")
        monkeypatch.setattr(resume_diagnostic_jobs, "enqueue_diagnostic_intent",
                            lambda *_args, **_kwargs: "retry_pending")
        monkeypatch.setattr(personal_graph_jobs, "request_personal_graph_projection",
                            lambda *_args, **_kwargs: {"status": "not_requested"})

        from app.pipeline.entity_extractor.l0_full_parser import L0FullParser
        from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
        from app.services.resume_agent_team import EntityAgent

        parsed = SimpleNamespace(
            success=True, text="示例大学 计算机科学 Python 课程项目", engine="fixture_parser",
            metadata={"pages": 1}, blocks=[SimpleNamespace(
                block_id="fixture-block-1", text="示例大学 计算机科学 Python 课程项目",
                page=0, block_type="text")],
        )
        candidate = {"name": "虚构学生", "school": "示例大学", "major": "计算机科学",
                     "degree": "本科", "skills": ["Python"],
                     "projects": [{"name": "课程项目", "desc": "编写课程查询接口"}]}
        monkeypatch.setattr(L0FullParser, "parse_file", lambda self, path: parsed)
        monkeypatch.setattr(NSLEPipeline, "__init__", lambda self, *args, **kwargs:
                            setattr(self, "_llm_client", object()))
        monkeypatch.setattr(NSLEPipeline, "run_resume_text", lambda self, text, *, link_entities=True: {
            "success": True, "student_profile": candidate,
            "structure_validation": {"ok": True},
        })
        monkeypatch.setattr(EntityAgent, "run", lambda self, profile, llm, document: profile)

        fixture_bytes = b"Synthetic resume: Example University; Python course project.\n"

        def reopen_student_session():
            nonlocal student
            saved_cookies = student.cookies.copy()
            student.close()
            student = requests.Session()
            student.trust_env = False
            student.cookies.update(saved_cookies)

        def upload_and_process():
            uploaded = student.post(
                f"{base}/api/profile/student/upload?async=1",
                files={"file": ("synthetic-resume.txt", fixture_bytes, "text/plain")},
                headers={"X-CSRFToken": csrf}, timeout=10,
            )
            assert uploaded.status_code == 202, uploaded.text
            body = uploaded.json()["data"]
            task_id = body["task_id"]
            assert body["student_id"] == owner and body["status"] == "pending"
            task = mysql.fetch_one(
                "SELECT pipeline,status,progress,params_json,student_id FROM pipeline_task "
                "WHERE task_id=:tid", {"tid": task_id},
            )
            assert task and task["student_id"] == owner
            assert (task["pipeline"], task["status"], task["progress"]) == (
                "L0_parse_resume", "pending", 0)
            params = json.loads(task["params_json"])
            saved_path = Path(params["file_path"]).resolve()
            assert params["student_id"] == owner
            assert saved_path.is_relative_to(Path(tmp_path).resolve())
            assert saved_path.read_bytes() == fixture_bytes
            queued = student.get(f"{base}/api/profile/student/workbench", timeout=10)
            assert queued.status_code == 200, queued.text
            assert queued.json()["data"]["run"]["state"] == "queued"
            # Model a user closing the page and returning later with the same
            # authenticated browser cookie, rather than retaining page state.
            reopen_student_session()
            reopened = student.get(f"{base}/api/profile/student/workbench", timeout=10)
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()["data"]["run"]["task_id"] == task_id
            stranger_state = stranger.get(f"{base}/api/profile/student/workbench", timeout=5)
            assert stranger_state.status_code == 200, stranger_state.text
            assert stranger_state.json()["data"]["source_confirmation_task_id"] is None
            assert stranger.get(f"{base}/api/profile/student/candidate/{task_id}",
                                timeout=5).status_code == 404

            from app.services.pipeline_resume_jobs import execute_resume_intent
            processed = execute_resume_intent(task_id, mysql=mysql)
            assert processed["status"] == "needs_input", processed
            candidate_http = student.get(f"{base}/api/profile/student/candidate/{task_id}", timeout=10)
            assert candidate_http.status_code == 200, candidate_http.text
            assert candidate_http.json()["data"]["candidate_profile"]["skills"] == ["Python"]
            assert stranger.get(f"{base}/api/pipeline/trace/{task_id}", timeout=5).status_code == 404
            return task_id

        first_task_id = upload_and_process()
        before_confirm = student.get(f"{base}/api/profile/student/workbench", timeout=10).json()["data"]
        assert before_confirm["run"]["state"] == "waiting_for_user"
        assert before_confirm["run"]["task_id"] == first_task_id

        confirmed = student.post(
            f"{base}/api/profile/student/confirm",
            json={"task_id": first_task_id, "confirmed": True},
            headers={"X-CSRFToken": csrf}, timeout=30,
        )
        assert confirmed.status_code == 200, confirmed.text
        first_portrait_task = confirmed.json()["data"]["portrait_analysis"]["task_id"]
        first_diagnostic_task = confirmed.json()["data"]["resume_diagnostic_analysis"]["task_id"]
        repeated_confirmation = student.post(
            f"{base}/api/profile/student/confirm",
            json={"task_id": first_task_id, "confirmed": True},
            headers={"X-CSRFToken": csrf}, timeout=30,
        )
        assert repeated_confirmation.status_code == 200, repeated_confirmation.text
        assert repeated_confirmation.json()["data"]["portrait_analysis"]["task_id"] == first_portrait_task
        assert repeated_confirmation.json()["data"]["resume_diagnostic_analysis"]["task_id"] == first_diagnostic_task
        assert mysql.fetch_one(
            "SELECT COUNT(*) AS count FROM pipeline_task WHERE student_id=:sid "
            "AND task_id IN (:portrait,:diagnostic)",
            {"sid": owner, "portrait": first_portrait_task, "diagnostic": first_diagnostic_task},
        )["count"] == 2

        class UnavailableModel:
            def chat_with_metadata(self, **_kwargs):
                raise RuntimeError("controlled model outage")

        first_portrait = pipeline_portrait_jobs.execute_portrait_intent(first_portrait_task, mysql=mysql)
        assert first_portrait["status"] == "partial", first_portrait
        assert pipeline_portrait_jobs.execute_portrait_intent(
            first_portrait_task, mysql=mysql)["status"] == "already_claimed_or_terminal"
        first_diagnostic = resume_diagnostic_jobs.execute_diagnostic_intent(
            first_diagnostic_task, mysql=mysql, llm_client=UnavailableModel())
        assert first_diagnostic["status"] == "partial", first_diagnostic

        first_state_response = student.get(f"{base}/api/profile/student/workbench", timeout=10)
        assert first_state_response.status_code == 200, first_state_response.text
        first_state = first_state_response.json()["data"]
        assert first_state["source_confirmation_task_id"] == first_task_id
        assert first_state["portrait"]["state"] == "ready_with_unknowns"
        assert first_state["portrait"]["scoring_eligible"] is False
        assert first_state["portrait"]["dimensions"]["D1"] is None
        assert first_state["portrait"]["dimensions"]["D4"] is None
        assert first_state["resume_diagnostic"]["state"] == "partial"
        first_snapshot_id = first_state["portrait"]["snapshot_id"]
        assert first_snapshot_id.startswith("ps_")

        report_http = student.get(f"{base}/api/report/resume-diagnostic", timeout=10)
        assert report_http.status_code == 200, report_http.text
        assert report_http.json()["data"]["status"] == "partial"
        report_id = report_http.json()["data"]["report_id"]
        report_readback = student.get(f"{base}/api/report/resume-diagnostic/{report_id}", timeout=10)
        assert report_readback.status_code == 200, report_readback.text
        assert report_readback.json()["data"]["source_confirmation_task_id"] == first_task_id
        snapshot_http = student.get(
            f"{base}/api/profile/student/portrait-snapshots/{first_snapshot_id}", timeout=10)
        assert snapshot_http.status_code == 200, snapshot_http.text
        assert snapshot_http.json()["data"]["is_current"] is True
        assert mysql.fetch_one(
            "SELECT id FROM student_portrait_4d WHERE student_id=:sid", {"sid": owner}) is None
        assert mysql.fetch_one(
            "SELECT report_id FROM resume_diagnostic_report WHERE report_id=:rid AND student_id=:sid",
            {"rid": report_id, "sid": owner}) is not None
        assert stranger.get(f"{base}/api/report/resume-diagnostic/{report_id}",
                            timeout=5).status_code == 404
        assert stranger.get(
            f"{base}/api/profile/student/portrait-snapshots/{first_snapshot_id}",
            timeout=5).status_code == 404

        # A second real upload/confirmation switches the current source while
        # the previous snapshot remains owner-readable as historical data.
        second_task_id = upload_and_process()
        before_second_confirm = student.get(f"{base}/api/profile/student/workbench", timeout=10).json()["data"]
        assert before_second_confirm["based_on_previous_upload"] is True
        assert before_second_confirm["source_confirmation_task_id"] == first_task_id
        second_confirmed = student.post(
            f"{base}/api/profile/student/confirm",
            json={"task_id": second_task_id, "confirmed": True},
            headers={"X-CSRFToken": csrf}, timeout=30,
        )
        assert second_confirmed.status_code == 200, second_confirmed.text
        old_snapshot = student.get(
            f"{base}/api/profile/student/portrait-snapshots/{first_snapshot_id}", timeout=10)
        assert old_snapshot.status_code == 200, old_snapshot.text
        assert old_snapshot.json()["data"]["is_current"] is False
        second_portrait_task = second_confirmed.json()["data"]["portrait_analysis"]["task_id"]
        second_portrait = pipeline_portrait_jobs.execute_portrait_intent(second_portrait_task, mysql=mysql)
        assert second_portrait["status"] == "partial", second_portrait
        final_state = student.get(f"{base}/api/profile/student/workbench", timeout=10).json()["data"]
        assert final_state["source_confirmation_task_id"] == second_task_id
        assert final_state["portrait"]["snapshot_id"] not in {None, first_snapshot_id}
        snapshots = student.get(f"{base}/api/profile/student/portrait-snapshots", timeout=10).json()["data"]
        assert snapshots["current_snapshot_id"] == final_state["portrait"]["snapshot_id"]
        assert len(snapshots["items"]) == 2
        assert stranger.get(
            f"{base}/api/profile/student/portrait-snapshots/{first_snapshot_id}",
            timeout=5).status_code == 404
    finally:
        if student:
            student.close()
        if stranger:
            stranger.close()
        if server:
            server.shutdown()
        if thread:
            thread.join(timeout=5)
        try:
            if mysql:
                mysql.close()
            if database_created and admin:
                with admin.cursor() as cursor:
                    cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        finally:
            if admin:
                admin.close()
            # The reserved DB was required to be empty before this test.
            # Delete only keys created since then; never FLUSHDB.
            try:
                created_keys = list(set(redis_client.scan_iter()) - baseline_redis_keys)
                if created_keys:
                    redis_client.delete(*created_keys)
            finally:
                redis_client.close()
