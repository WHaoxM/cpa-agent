"""Opt-in MySQL regression for L3 durable intent, redelivery and fencing.

Run with CPA_TEST_LIVE_MYSQL=1. Each case owns and removes UUID-scoped rows.
"""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import event

from app.services.db.mysql_client import get_mysql_client
from app.services.pipeline import BasePipeline, PipelineStatus
from app.services import pipeline_match_jobs as jobs


class _Probe(BasePipeline):
    def run(self):
        raise NotImplementedError


@pytest.fixture
def intent():
    db = get_mysql_client()
    owner = "match_dispatch_" + uuid.uuid4().hex[:12]
    task_id = "task_" + uuid.uuid4().hex[:20]
    try:
        yield db, owner, task_id
    finally:
        db.execute("DELETE FROM pipeline_match_dispatch WHERE task_id=:tid", {"tid": task_id}, commit=True)
        db.execute("DELETE FROM pipeline_task_event WHERE task_id=:tid", {"tid": task_id}, commit=True)
        db.execute("DELETE FROM pipeline_task WHERE task_id=:tid", {"tid": task_id}, commit=True)
        assert not db.fetch_all("SELECT task_id FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL")
def test_broker_failure_is_replayable_and_expired_claim_is_fenced(intent, monkeypatch):
    db, owner, task_id = intent
    jobs.persist_match_intent(db, student_id=owner, job_portrait_id="fixture_job", task_id=task_id)
    assert db.fetch_all("SELECT status FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})[0]["status"] == "pending"

    def broker_down(*args, **kwargs):
        raise RuntimeError("injected broker outage")

    monkeypatch.setattr(jobs.match_task, "apply_async", broker_down)
    assert jobs.enqueue_match_intent(db, task_id) == "retry_pending"
    replay = jobs.replay_match_intents(db, student_id=owner)
    assert replay["attempted"] == 1 and replay["dispatched"] == 0

    first = jobs.claim_match_intent(db, task_id)
    assert first and first["student_id"] == owner
    assert jobs.claim_match_intent(db, task_id) is None
    original = _Probe(task_id, {"student_id": owner}, db)
    original.claim_token = first["claim_token"]
    original.update_status(PipelineStatus.RUNNING)

    # Simulate a worker disappearing after claim. A delayed old worker must
    # never overwrite the recovered task's result or append a false event.
    db.execute("UPDATE pipeline_match_dispatch SET lease_until=NOW(6)-INTERVAL 1 SECOND WHERE task_id=:tid",
               {"tid": task_id}, commit=True)
    second = jobs.claim_match_intent(db, task_id)
    assert second and second["claim_token"] != first["claim_token"]
    with pytest.raises(RuntimeError, match="状态转换"):
        original.update_status(PipelineStatus.RUNNING)

    recovered = _Probe(task_id, {"student_id": owner}, db)
    recovered.claim_token = second["claim_token"]
    recovered.update_status(PipelineStatus.RUNNING)
    with pytest.raises(RuntimeError, match="状态转换"):
        original.update_status(PipelineStatus.PARTIAL, {"status": "partial", "forged": True})
    recovered.update_status(PipelineStatus.PARTIAL, {"status": "partial", "verified": False})
    row = db.fetch_all("SELECT status,result_json FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})[0]
    assert row["status"] == "partial"
    assert json.loads(row["result_json"])["verified"] is False
    events = db.fetch_all("SELECT status FROM pipeline_task_event WHERE task_id=:tid ORDER BY event_id",
                          {"tid": task_id})
    assert [e["status"] for e in events] == ["pending", "running", "pending", "running", "partial"]
    assert jobs.claim_match_intent(db, task_id) is None


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL")
def test_dispatch_insert_failure_rolls_back_task_and_event(intent):
    db, owner, task_id = intent
    def fail_dispatch(_conn, _cursor, statement, _params, _context, _many):
        if "INSERT INTO pipeline_match_dispatch" in statement:
            raise RuntimeError("injected migration/dispatch write failure")
    event.listen(db.engine, "before_cursor_execute", fail_dispatch)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            jobs.persist_match_intent(db, student_id=owner, job_portrait_id="fixture_job", task_id=task_id)
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_dispatch)
    assert db.fetch_all("SELECT task_id FROM pipeline_task WHERE task_id=:tid", {"tid": task_id}) == []
    assert db.fetch_all("SELECT status FROM pipeline_task_event WHERE task_id=:tid", {"tid": task_id}) == []


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL")
def test_post_commit_enqueue_read_failure_keeps_replayable_intent(intent, monkeypatch):
    db, owner, task_id = intent
    jobs.persist_match_intent(db, student_id=owner, job_portrait_id="fixture_job", task_id=task_id)
    original_fetch = db.fetch_one
    def fail_read(*args, **kwargs):
        raise RuntimeError("injected post-commit DB read failure")
    monkeypatch.setattr(db, "fetch_one", fail_read)
    assert jobs.enqueue_match_intent(db, task_id) == "unverified"
    monkeypatch.setattr(db, "fetch_one", original_fetch)
    row = db.fetch_one("SELECT status FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
    assert row["status"] == "pending"
    def fake_publish(*args, **kwargs):
        return None
    monkeypatch.setattr(jobs.match_task, "apply_async", fake_publish)
    replay = jobs.replay_match_intents(db, student_id=owner)
    assert replay["attempted"] == replay["dispatched"] == 1


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL")
def test_http_post_commit_enqueue_failure_still_returns_task_id(monkeypatch):
    from flask import g
    from app import create_app
    from app.api._helpers import Principal
    from app.config import Config
    from app.services import pipeline as domain

    db = get_mysql_client()
    owner = "match_enqueue_" + uuid.uuid4().hex[:12]
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr(domain, "require_confirmed_profile", lambda _db, _owner: {"confirmed": True})
    monkeypatch.setattr(domain, "require_authoritative_match_sources", lambda *_args: {"verified": True})
    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("injected post-commit delivery failure")
    monkeypatch.setattr(jobs, "enqueue_match_intent", fail_enqueue)
    app = create_app()
    @app.before_request
    def principal():
        g.cpa_principal = Principal(owner, owner, "live-test")
    try:
        response = app.test_client().post("/api/pipeline/l3/run-match", json={"job_portrait_id": "fixture_job"})
        assert response.status_code == 202, response.json
        task_id = response.json["data"]["task_id"]
        assert response.json["data"]["delivery"] == "unverified"
        row = db.fetch_one("SELECT status,student_id FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
        assert row == {"status": "pending", "student_id": owner}
        assert db.fetch_one("SELECT COUNT(*) n FROM pipeline_match_dispatch WHERE task_id=:tid", {"tid": task_id})["n"] == 1
    finally:
        rows = db.fetch_all("SELECT task_id FROM pipeline_task WHERE student_id=:sid", {"sid": owner})
        for row in rows:
            db.execute("DELETE FROM pipeline_match_dispatch WHERE task_id=:tid", {"tid": row["task_id"]}, commit=True)
        db.execute("DELETE FROM pipeline_task_event WHERE student_id=:sid", {"sid": owner}, commit=True)
        db.execute("DELETE FROM pipeline_task WHERE student_id=:sid", {"sid": owner}, commit=True)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires live MySQL")
def test_bounded_automatic_replay_does_not_starve_later_intents(monkeypatch):
    db = get_mysql_client()
    owner = "match_fair_" + uuid.uuid4().hex[:12]
    tasks = ["task_" + uuid.uuid4().hex[:20] for _ in range(51)]
    def broker_down(*args, **kwargs):
        raise RuntimeError("injected broker outage")
    monkeypatch.setattr(jobs.match_task, "apply_async", broker_down)
    try:
        for task_id in tasks:
            jobs.persist_match_intent(db, student_id=owner, job_portrait_id="fixture_job", task_id=task_id)
        first = jobs.replay_match_intents(db, student_id=owner, limit=50, force=False)
        second = jobs.replay_match_intents(db, student_id=owner, limit=50, force=False)
        third = jobs.replay_match_intents(db, student_id=owner, limit=50, force=False)
        assert first["attempted"] == 50 and first["dispatched"] == 0
        assert second["attempted"] == 1 and second["dispatched"] == 0
        assert third["attempted"] == 0
        assert {o["task_id"] for o in first["results"] + second["results"]} == set(tasks)
    finally:
        for task_id in tasks:
            db.execute("DELETE FROM pipeline_match_dispatch WHERE task_id=:tid", {"tid": task_id}, commit=True)
        db.execute("DELETE FROM pipeline_task_event WHERE student_id=:sid", {"sid": owner}, commit=True)
        db.execute("DELETE FROM pipeline_task WHERE student_id=:sid", {"sid": owner}, commit=True)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MATCH_QUEUE") != "1", reason="requires live MySQL/Redis worker opt-in")
@pytest.mark.parametrize("delivery_mode", ["normal", "manual_replay", "auto_replay_pending", "auto_replay_running"])
def test_http_intent_real_worker_domain_result_and_owner_read(monkeypatch, tmp_path, delivery_mode):
    from flask import g, request
    from app import create_app
    from app.api._helpers import Principal
    from app.config import Config
    from app.services.job_publication import canonical_json, publish_job
    from backend.tests.test_verified_match_report import snapshot

    if delivery_mode.startswith("auto_"):
        assert jobs.celery_app.conf.beat_schedule["pipeline-match-replay"]["task"] == "cpa.pipeline.replay"

    db = get_mysql_client()
    tag = "match_worker_" + uuid.uuid4().hex[:12]
    sid, jid, l0, l2 = tag, tag + "_job", tag + "_l0", tag + "_l2"
    queue = "cpa.pipeline.test." + uuid.uuid4().hex
    monkeypatch.setattr(jobs, "MATCH_QUEUE", queue)
    worker = None
    beat = None
    task_id = None
    try:
        source = "我负责课程团队项目，和组员沟通协作，在紧急截止日前完成交付。"
        digest = hashlib.sha256(source.encode()).hexdigest()
        facts = {k: {"value": "中", "evidence": ev, "source_sha256": digest}
                 for k, ev in {"communication": "沟通协作", "teamwork": "团队项目",
                               "stress_tolerance": "紧急截止日前完成交付",
                               "responsibility": "我负责课程团队项目"}.items()}
        candidate = {"skills": [{"name": "Python", "level": "掌握", "confidence": 1.0}],
                     "degree": "专科", "source_sha256": digest, "semantic_facts": facts}
        profile = {**candidate, "_resume_confirmation": {"student_id": sid, "task_id": l0}}
        db.execute("INSERT INTO student_profile(student_id,profile_json) VALUES (:sid,:payload)",
                   {"sid": sid, "payload": canonical_json(profile)}, commit=True)
        portrait = {"source_confirmation_task_id": l0, "source_portrait_task_id": l2}
        db.execute("INSERT INTO student_portrait_4d(student_id,target_job_id,portrait_json) "
                   "VALUES (:sid,:jid,:payload)",
                   {"sid": sid, "jid": jid, "payload": canonical_json(portrait)}, commit=True)
        portrait_id = str(db.fetch_one("SELECT id FROM student_portrait_4d WHERE student_id=:sid", {"sid": sid})["id"])
        for tid, level, result in ((l0, "L0_parse_resume", {"confirmed": True, "candidate_profile": candidate}),
                                   (l2, "L2_generate_portrait", {"portrait_id": portrait_id, "portrait": portrait})):
            db.execute("INSERT INTO pipeline_task(task_id,pipeline,status,student_id,result_json,params_json) "
                       "VALUES (:tid,:level,'completed',:sid,:result,'{}')",
                       {"tid": tid, "level": level, "sid": sid, "result": canonical_json(result)}, commit=True)
        publish_job(db, job_portrait_id=jid, version="match-worker-test", snapshot=snapshot())
        monkeypatch.setattr(Config, "DEMO_MODE", False)
        app = create_app()

        @app.before_request
        def principal():
            owner = request.headers.get("X-Test-Owner", sid)
            g.cpa_principal = Principal(owner, owner, "live-test")

        client = app.test_client()
        original_publish = jobs.match_task.apply_async
        broker_outage = delivery_mode != "normal"
        if broker_outage:
            def broker_down(*args, **kwargs):
                raise RuntimeError("injected first publish outage")
            monkeypatch.setattr(jobs.match_task, "apply_async", broker_down)
        response = client.post("/api/pipeline/l3/run-match", json={
            "job_portrait_id": jid, "student_id": "forged_owner", "total_score": 100,
        })
        assert response.status_code == 202, response.json
        task_id = response.json["data"]["task_id"]
        assert response.json["data"]["delivery"] == ("retry_pending" if broker_outage else "queued")
        if broker_outage:
            monkeypatch.setattr(jobs.match_task, "apply_async", original_publish)
            if delivery_mode == "manual_replay":
                replay = jobs.replay_match_intents(db, student_id=sid)
                assert replay["attempted"] == replay["dispatched"] == 1
            elif delivery_mode == "auto_replay_running":
                claim = jobs.claim_match_intent(db, task_id)
                assert claim is not None
                probe = _Probe(task_id, {"student_id": sid}, db)
                probe.claim_token = claim["claim_token"]
                probe.update_status(PipelineStatus.RUNNING)
                db.execute("UPDATE pipeline_match_dispatch SET lease_until=NOW(6)-INTERVAL 1 SECOND "
                           "WHERE task_id=:tid", {"tid": task_id}, commit=True)
            if delivery_mode.startswith("auto_"):
                # Fast-forward only the retry due time; the Beat process, not
                # this test, must discover and enqueue the durable intent.
                db.execute("UPDATE pipeline_match_dispatch SET last_enqueued_at=NOW(6)-INTERVAL 31 SECOND "
                           "WHERE task_id=:tid", {"tid": task_id}, commit=True)
        assert client.get(f"/api/pipeline/status/{task_id}", headers={"X-Test-Owner": sid + "_other"}).status_code == 404

        backend = str(Path(__file__).parents[1])
        env = {**os.environ, "PYTHONPATH": backend, "PYTHONUTF8": "1",
               "CPA_PIPELINE_QUEUE": queue, "CPA_PIPELINE_REPLAY_INTERVAL_SECONDS": "5"}
        log_path = tmp_path / "match-worker.log"
        with log_path.open("w", encoding="utf-8") as log:
            worker = subprocess.Popen([sys.executable, "-B", str(Path(backend) / "tests" / "fixtures" / "match_queue_worker.py"),
                "--pool=solo", "--concurrency=1",
                "--queues=" + queue, "--hostname=" + queue, "--loglevel=INFO", "--without-gossip", "--without-mingle"],
                cwd=backend, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if delivery_mode.startswith("auto_"):
                beat_log = tmp_path / "match-beat.log"
                with beat_log.open("w", encoding="utf-8") as beat_output:
                    beat = subprocess.Popen([sys.executable, "-B", "-m", "celery", "-A",
                        "app.services.agent_runtime.media.jobs:celery_app", "beat",
                        "--schedule=" + str(tmp_path / "match-beat-schedule"), "--loglevel=INFO"],
                        cwd=backend, env=env, stdout=beat_output, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                row = db.fetch_one("SELECT status FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
                if row["status"] in ("partial", "completed", "failed"):
                    break
                assert worker.poll() is None, log_path.read_text(encoding="utf-8")
                if beat is not None:
                    assert beat.poll() is None, beat_log.read_text(encoding="utf-8")
                time.sleep(0.5)
            assert row["status"] == "partial", (row, log_path.read_text(encoding="utf-8"),
                                                beat_log.read_text(encoding="utf-8") if beat is not None else "")
        rows = db.fetch_all("SELECT id,result_json FROM match_result WHERE student_id=:sid AND job_portrait_id=:jid",
                            {"sid": sid, "jid": jid})
        assert len(rows) == 1
        stored = json.loads(rows[0]["result_json"])
        assert stored["student_id"] == sid and stored["job_portrait_id"] == jid
        assert stored["source_match_task_id"] == task_id
        assert stored["total_score"] != 100
        read = client.get(f"/api/pipeline/status/{task_id}")
        assert read.status_code == 200 and read.json["data"]["result"]["match_id"] == str(rows[0]["id"])
        events = client.get(f"/api/pipeline/events/{task_id}")
        assert events.status_code == 200
        expected_events = (["pending", "running", "pending", "running", "partial"]
                           if delivery_mode == "auto_replay_running" else ["pending", "running", "partial"])
        assert [e["status"] for e in events.json["data"]["events"]] == expected_events
        assert client.get(f"/api/pipeline/events/{task_id}", headers={"X-Test-Owner": sid + "_other"}).status_code == 404
        assert jobs.execute_match_intent(task_id, mysql=db)["status"] == "already_claimed_or_terminal"
        assert db.fetch_one("SELECT COUNT(*) n FROM match_result WHERE student_id=:sid", {"sid": sid})["n"] == 1
    finally:
        if beat is not None:
            beat.terminate()
            beat.wait(timeout=15)
        if worker is not None:
            worker.terminate()
            worker.wait(timeout=15)
        if task_id:
            db.execute("DELETE FROM pipeline_match_dispatch WHERE task_id=:tid", {"tid": task_id}, commit=True)
        db.execute("DELETE FROM match_result WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM pipeline_task_event WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM pipeline_task WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM student_portrait_4d WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM student_profile WHERE student_id=:sid", {"sid": sid}, commit=True)
        db.execute("DELETE FROM job_catalog_publication WHERE job_portrait_id=:jid", {"jid": jid}, commit=True)
        with jobs.celery_app.connection_for_write() as connection:
            connection.default_channel.queue_delete(queue=queue)
