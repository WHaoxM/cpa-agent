"""Opt-in Redis / worker / Beat smoke without writes to existing business DBs."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest


BACKEND = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
REQUIRED_TASKS = {
    "cpa.media.execute",
    "cpa.graph.project",
    "cpa.graph.project_entity_links",
    "cpa.pipeline.match",
    "cpa.pipeline.replay",
    "cpa.pipeline.resume",
    "cpa.pipeline.resume_replay",
    "cpa.pipeline.portrait",
    "cpa.pipeline.portrait_replay",
    "cpa.pipeline.diagnostic",
    "cpa.pipeline.diagnostic_replay",
    "cpa.catalog.refresh_source_feed",
    "cpa.test.transport_probe",
}


def test_production_beat_tasks_are_registered_and_routed():
    from app.services.agent_runtime.media.jobs import PIPELINE_QUEUE, celery_app

    celery_app.loader.import_default_modules()
    schedule = celery_app.conf.beat_schedule
    assert len(schedule) == 5
    for entry in schedule.values():
        assert entry["task"] in celery_app.tasks
        assert entry["options"]["queue"] == PIPELINE_QUEUE
    for task, queue in {
        "cpa.media.execute": "cpa.media",
        "cpa.graph.project": "cpa.graph",
        "cpa.graph.project_entity_links": "cpa.graph",
        "cpa.pipeline.match": PIPELINE_QUEUE,
        "cpa.pipeline.resume": PIPELINE_QUEUE,
        "cpa.pipeline.portrait": PIPELINE_QUEUE,
        "cpa.pipeline.diagnostic": PIPELINE_QUEUE,
        "cpa.catalog.refresh_source_feed": PIPELINE_QUEUE,
    }.items():
        assert celery_app.amqp.router.route({}, task)["queue"].name == queue


def _stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_CELERY") != "1",
                    reason="Set CPA_TEST_LIVE_CELERY=1 for isolated real worker/Beat smoke")
def test_beat_message_is_executed_by_real_worker(tmp_path):
    import redis

    # A dedicated Redis logical DB and UUID queue avoid consuming any normal
    # cpa.pipeline/media/graph message. Refuse to use an occupied test DB.
    broker = os.getenv("CPA_TEST_CELERY_BROKER_URL", "redis://127.0.0.1:16379/14")
    client = redis.Redis.from_url(broker, socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() and client.dbsize() == 0, "Celery test broker DB must be empty"
    unique = uuid.uuid4().hex[:12]
    queue = f"cpa.smoke.{unique}"
    node = f"cpa-smoke-{unique}@localhost"
    marker = tmp_path / "worker-executed.txt"
    schedule = tmp_path / "celerybeat-schedule"
    env = os.environ.copy()
    env.update({
        "CPA_MEDIA_BROKER_URL": broker,
        "CPA_CELERY_PROBE_QUEUE": queue,
        "CPA_CELERY_PROBE_MARKER": str(marker),
        "PYTHONPATH": os.pathsep.join((str(TESTS), str(BACKEND), env.get("PYTHONPATH", ""))),
        "PYTHONUTF8": "1",
    })
    command = [sys.executable, "-B", "-m", "celery", "-A", "celery_transport_probe:celery_app"]
    worker = beat = None
    try:
        worker = subprocess.Popen(command + ["worker", "--pool=solo", "--concurrency=1",
                                           f"--queues={queue}", f"--hostname={node}",
                                           "--loglevel=WARNING"], cwd=BACKEND, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        # Import from the test helper but inspect the *running* worker's task
        # registry, not merely the locally importable Celery configuration.
        from celery import Celery

        control_app = Celery("cpa_smoke_control", broker=broker)

        registered = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(f"Celery worker exited early with code {worker.returncode}")
            responses = control_app.control.inspect(destination=[node], timeout=2).registered() or {}
            if responses:
                registered = {task.split("[")[0] for task in responses[node]}
                break
            time.sleep(1)
        assert registered is not None, "Celery worker did not become inspectable"
        assert REQUIRED_TASKS <= registered

        beat = subprocess.Popen(command + ["beat", f"--schedule={schedule}",
                                         "--loglevel=WARNING"], cwd=BACKEND, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not marker.exists():
            if beat.poll() is not None:
                pytest.fail(f"Celery Beat exited early with code {beat.returncode}")
            if worker.poll() is not None:
                pytest.fail(f"Celery worker exited with code {worker.returncode}")
            time.sleep(0.5)
        assert marker.read_text(encoding="utf-8") == "worker-executed"
    finally:
        _stop(beat)
        _stop(worker)
        # Only the UUID-scoped broker keys can belong to this test. Never
        # FLUSHDB a shared Redis instance, even when it was initially empty.
        for key in client.scan_iter(match=f"*{unique}*"):
            client.delete(key)
        # Celery's event/control exchanges leave these two shared-name
        # bindings even after the probe worker exits. This DB was required to
        # be empty before start and is reserved for the smoke, so remove only
        # the exact bindings it created rather than flushing the whole DB.
        client.delete("_kombu.binding.celeryev", "_kombu.binding.celery.pidbox")


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_CELERY") != "1" or
                    os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_CELERY=1 and CPA_TEST_LIVE_MYSQL=1")
def test_real_match_worker_persists_source_blocked_result_in_disposable_mysql():
    """Run a production L3 task, then read its owner-bound MySQL result.

    A deliberately absent L2 portrait must yield partial, not a match. This
    proves real worker execution without a model call or business DB write.
    """
    import pymysql
    import redis
    from celery import Celery
    from app.config import Config

    broker = os.getenv("CPA_TEST_CELERY_BUSINESS_BROKER_URL", "redis://127.0.0.1:16379/13")
    redis_client = redis.Redis.from_url(broker, socket_connect_timeout=2, socket_timeout=2)
    assert redis_client.ping() and redis_client.dbsize() == 0, "Business probe broker DB must be empty"
    unique = uuid.uuid4().hex[:12]
    database = f"cpa_celery_test_{unique}"
    queue = f"cpa.smoke.{unique}"
    node = f"cpa-business-{unique}@localhost"
    task_id = f"task_celery_{unique}"
    student_id = f"student_celery_{unique}"
    confirmation_id = f"task_confirm_{unique}"
    job_id = f"job_missing_{unique}"
    admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            autocommit=True)
    database_conn = worker = None
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
                        "-x", f"database={database}", "upgrade", "head"],
                       cwd=BACKEND, check=True, capture_output=True, text=True)
        database_conn = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                                        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                                        database=database, autocommit=True)
        candidate = {"name": "Celery probe"}
        profile = {**candidate, "_resume_confirmation": {
            "task_id": confirmation_id, "student_id": student_id}}
        with database_conn.cursor() as cursor:
            cursor.execute("INSERT INTO student_profile(student_id,profile_json) VALUES (%s,%s)",
                           (student_id, json.dumps(profile)))
            cursor.execute("INSERT INTO pipeline_task(task_id,pipeline,status,student_id,result_json) "
                           "VALUES (%s,'L0_parse_resume','completed',%s,%s)",
                           (confirmation_id, student_id, json.dumps({
                               "confirmed": True, "candidate_profile": candidate})))
            cursor.execute("INSERT INTO pipeline_task(task_id,pipeline,status,student_id,job_id,params_json) "
                           "VALUES (%s,'L3_run_match','pending',%s,%s,%s)",
                           (task_id, student_id, job_id, json.dumps({
                               "student_id": student_id, "job_id": job_id})))
            cursor.execute("INSERT INTO pipeline_match_dispatch(task_id) VALUES (%s)", (task_id,))

        env = os.environ.copy()
        env.update({
            "CPA_MEDIA_BROKER_URL": broker,
            "CPA_CELERY_PROBE_QUEUE": queue,
            "CPA_CELERY_PROBE_MARKER": os.devnull,
            "CPA_CELERY_PROBE_DATABASE": database,
            "PYTHONPATH": os.pathsep.join((str(TESTS), str(BACKEND), env.get("PYTHONPATH", ""))),
            "PYTHONUTF8": "1",
        })
        command = [sys.executable, "-B", "-m", "celery", "-A", "celery_transport_probe:celery_app"]
        worker = subprocess.Popen(command + ["worker", "--pool=solo", "--concurrency=1",
                                           f"--queues={queue}", f"--hostname={node}",
                                           "--loglevel=WARNING"], cwd=BACKEND, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        control_app = Celery("cpa_business_control", broker=broker)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(f"Business worker exited early with code {worker.returncode}")
            response = control_app.control.inspect(destination=[node], timeout=2).registered() or {}
            if node in response:
                assert "cpa.pipeline.match" in {task.split("[")[0] for task in response[node]}
                break
            time.sleep(1)
        else:
            pytest.fail("Business worker did not become inspectable")

        control_app.send_task("cpa.pipeline.match", args=[task_id], queue=queue,
                              task_id=f"cpa-match-{task_id}")
        deadline = time.monotonic() + 45
        status = result = attempts = None
        while time.monotonic() < deadline:
            with database_conn.cursor() as cursor:
                cursor.execute("SELECT t.status,t.result_json,d.attempts FROM pipeline_task t "
                               "JOIN pipeline_match_dispatch d ON d.task_id=t.task_id "
                               "WHERE t.task_id=%s AND t.student_id=%s", (task_id, student_id))
                status, result, attempts = cursor.fetchone()
            if status in ("partial", "failed", "completed"):
                break
            if worker.poll() is not None:
                pytest.fail(f"Business worker exited with code {worker.returncode}")
            time.sleep(0.5)
        assert status == "partial"
        assert attempts == 1
        result = json.loads(result) if isinstance(result, str) else result
        assert result["reason"] == "confirmed_portrait_unavailable"
        assert result["student_id"] == student_id
        assert result["resource_id"] == task_id
        with database_conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM match_result WHERE student_id=%s", (student_id,))
            assert cursor.fetchone() == (0,)
    finally:
        _stop(worker)
        if database_conn is not None:
            database_conn.close()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        for key in redis_client.scan_iter(match=f"*{unique}*"):
            redis_client.delete(key)
        redis_client.delete("_kombu.binding.celeryev", "_kombu.binding.celery.pidbox")
