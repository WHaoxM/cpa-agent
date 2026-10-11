"""Post-commit confirmation delivery, durable retries and actual Celery worker."""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services import personal_graph_jobs as jobs
from app.services.personal_graph import query_personal_skills
from app.services.pipeline import confirm_resume_candidate, require_confirmed_profile


@pytest.fixture
def live():
    if os.getenv("CPA_TEST_LIVE_PERSONAL_GRAPH") != "1":
        pytest.skip("real MySQL and Neo4j opt-in required")
    from app.services.db import get_mysql_client, get_neo4j_client
    from app.services.pipeline_events import install_pipeline_event_journal
    mysql, neo = get_mysql_client(), get_neo4j_client()
    install_pipeline_event_journal(mysql)
    jobs.install_personal_graph_projection(mysql)
    owner = "pgjob_" + uuid.uuid4().hex[:16]
    tasks = []

    def candidate(skills):
        tid = "task_" + uuid.uuid4().hex[:16]
        tasks.append(tid)
        result = {"candidate_profile": {"skills": skills}, "structure_validation": {"ok": True}, "source_evidence": {"blocks": []}}
        mysql.execute("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
                      "VALUES(:tid,:sid,'L0_parse_resume','needs_input',:result)",
                      {"tid": tid, "sid": owner, "result": json.dumps(result)}, commit=True)
        return tid
    try:
        yield mysql, neo, owner, candidate
    finally:
        neo.execute_query("MATCH(n) WHERE (n:PersonalGraphSnapshot OR n:PersonalSkill) AND n.owner_student_id=$sid DETACH DELETE n", {"sid": owner}, read_only=False)
        with mysql.engine.begin() as conn:
            conn.execute(text("DELETE FROM personal_graph_projection WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task_event WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM pipeline_task WHERE student_id=:sid"), {"sid": owner})
            conn.execute(text("DELETE FROM student_profile WHERE student_id=:sid"), {"sid": owner})


def test_confirmation_commit_precedes_delivery_and_broker_outage_is_durable(live, monkeypatch):
    mysql, neo, owner, candidate = live
    task = candidate(["Python"])

    def broker_down(**kwargs):
        # Separate DB connection sees the completed confirmation already committed.
        assert require_confirmed_profile(mysql, owner)["_resume_confirmation"]["task_id"] == task
        events = mysql.fetch_all("SELECT status FROM pipeline_task_event WHERE task_id=:tid AND status='completed'", {"tid": task})
        assert events
        raise ConnectionError("injected broker outage")
    monkeypatch.setattr(jobs.projection_task, "apply_async", broker_down)
    result = confirm_resume_candidate(mysql, student_id=owner, task_id=task)
    source_before = mysql.fetch_all("SELECT result_json FROM pipeline_task WHERE task_id=:tid", {"tid": task})
    assert result["status"] == "completed" and result["profile_ready"]
    assert result["personal_graph_projection"]["status"] == "retry_pending"
    assert jobs._task(mysql, owner, task)["personal_graph_projection"]["reason"] == "broker_unavailable"
    assert query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["status"] == "unavailable"

    # Explicit replay uses persistent source + journal; no in-memory task state.
    delivered = []
    monkeypatch.setattr(jobs.projection_task, "apply_async", lambda **kwargs: delivered.append(kwargs))
    assert jobs.replay_personal_graph_projections(mysql, student_id=owner)["dispatched"] == 1
    assert delivered[0]["args"] == [owner, task]
    recovered = jobs.execute_personal_graph_projection(student_id=owner, task_id=task, mysql=mysql, neo4j=neo)
    assert recovered["status"] == "completed"
    assert query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["rows"][0]["name"] == "Python"
    assert jobs.replay_personal_graph_projections(mysql, student_id=owner)["dispatched"] == 0
    assert mysql.fetch_all("SELECT result_json FROM pipeline_task WHERE task_id=:tid", {"tid": task}) == source_before


def test_graph_outage_retries_current_version_and_old_job_is_superseded(live, monkeypatch):
    mysql, neo, owner, candidate = live
    monkeypatch.setattr(jobs.projection_task, "apply_async", lambda **kwargs: None)
    first = candidate(["Python"])
    confirm_resume_candidate(mysql, student_id=owner, task_id=first)
    class Offline:
        def execute_query(self, *args, **kwargs):
            raise ConnectionError("injected Neo4j outage")
    assert jobs.execute_personal_graph_projection(student_id=owner, task_id=first, mysql=mysql, neo4j=Offline())["status"] == "retry_pending"
    assert require_confirmed_profile(mysql, owner)["skills"] == ["Python"]
    second = candidate(["Rust"])
    confirm_resume_candidate(mysql, student_id=owner, task_id=second)
    assert jobs.execute_personal_graph_projection(student_id=owner, task_id=first, mysql=mysql, neo4j=neo)["status"] == "superseded"
    assert query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["status"] == "unavailable"
    assert jobs.execute_personal_graph_projection(student_id=owner, task_id=second, mysql=mysql, neo4j=neo)["status"] == "completed"
    assert [r["name"] for r in query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["rows"]] == ["Rust"]


def test_crash_after_commit_before_dispatch_is_discoverable(live, monkeypatch):
    mysql, neo, owner, candidate = live
    task = candidate(["Neo4j"])
    monkeypatch.setattr(jobs, "request_personal_graph_projection", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("injected crash window")))
    result = confirm_resume_candidate(mysql, student_id=owner, task_id=task)
    assert result["status"] == "completed"
    assert "personal_graph_projection" not in jobs._task(mysql, owner, task)
    monkeypatch.undo()
    deliveries = []
    monkeypatch.setattr(jobs.projection_task, "apply_async", lambda **kw: deliveries.append(kw))
    assert jobs.replay_personal_graph_projections(mysql, student_id=owner)["dispatched"] == 1
    assert deliveries[0]["args"] == [owner, task]


def test_graph_commit_before_ack_is_safely_replayed(live, monkeypatch):
    mysql, neo, owner, candidate = live
    task = candidate(["Neo4j"])
    monkeypatch.setattr(jobs.projection_task, "apply_async", lambda **kw: None)
    confirm_resume_candidate(mysql, student_id=owner, task_id=task)
    original = jobs._record
    def crash_ack(db, sid, tid, state, **kwargs):
        if state["status"] == "completed":
            raise RuntimeError("injected graph commit before acknowledgement")
        return original(db, sid, tid, state, **kwargs)
    monkeypatch.setattr(jobs, "_record", crash_ack)
    with pytest.raises(RuntimeError, match="before acknowledgement"):
        jobs.execute_personal_graph_projection(student_id=owner, task_id=task, mysql=mysql, neo4j=neo)
    assert jobs._task(mysql, owner, task)["personal_graph_projection"]["status"] == "pending"
    assert query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["success"]
    monkeypatch.setattr(jobs, "_record", original)
    assert jobs.replay_personal_graph_projections(mysql, student_id=owner)["dispatched"] == 1
    assert jobs.execute_personal_graph_projection(student_id=owner, task_id=task, mysql=mysql, neo4j=neo)["status"] == "completed"
    assert neo.execute_query("MATCH(s:PersonalSkill {owner_student_id:$sid}) RETURN count(s) AS count", {"sid": owner}) == [{"count": 1}]


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_GRAPH_QUEUE") != "1", reason="explicit live Redis/Celery process opt-in required")
def test_real_celery_worker_consumes_post_confirmation_projection(live, monkeypatch, tmp_path):
    mysql, neo, owner, candidate = live
    queue = "cpa.graph.test." + uuid.uuid4().hex
    monkeypatch.setattr(jobs, "GRAPH_QUEUE", queue)
    task = candidate(["Python", "Neo4j"])
    # Confirm while worker is absent. Broker must retain the message durably.
    result = confirm_resume_candidate(mysql, student_id=owner, task_id=task)
    assert result["personal_graph_projection"]["status"] == "queued"
    backend = str(Path(__file__).parents[1])
    env = {**os.environ, "PYTHONPATH": backend, "PYTHONUTF8": "1"}
    log_path = tmp_path / "graph-worker.log"
    worker = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            worker = subprocess.Popen([sys.executable, "-B", "-m", "celery", "-A",
                "app.services.agent_runtime.media.jobs:celery_app", "worker", "--pool=solo", "--concurrency=1",
                "--queues=" + queue, "--hostname=" + queue, "--loglevel=INFO", "--without-gossip", "--without-mingle"],
                cwd=backend, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                state = jobs._task(mysql, owner, task).get("personal_graph_projection") or {}
                if state.get("status") == "completed":
                    break
                assert worker.poll() is None, log_path.read_text(encoding="utf-8")
                time.sleep(0.5)
            assert state.get("status") == "completed", log_path.read_text(encoding="utf-8")
            assert [r["name"] for r in query_personal_skills(neo, mysql=mysql, owner_student_id=owner)["rows"]] == ["Neo4j", "Python"]
    finally:
        if worker is not None:
            worker.terminate()
            worker.wait(timeout=15)
        # Only this test's random queue is removed, never the service queue.
        with jobs.celery_app.connection_for_write() as connection:
            connection.default_channel.queue_delete(queue=queue)
