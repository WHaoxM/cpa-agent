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
    "cpa.agent.run",
    "cpa.agent.replay",
    "cpa.catalog.refresh_source_feed",
    "cpa.test.transport_probe",
}


def test_production_beat_tasks_are_registered_and_routed():
    from app.services.agent_runtime.media.jobs import AGENT_QUEUE, PIPELINE_QUEUE, celery_app

    celery_app.loader.import_default_modules()
    schedule = celery_app.conf.beat_schedule
    assert len(schedule) == 6
    for name, entry in schedule.items():
        assert entry["task"] in celery_app.tasks
        expected_queue = AGENT_QUEUE if name == "agent-run-replay" else PIPELINE_QUEUE
        assert entry["options"]["queue"] == expected_queue
    for task, queue in {
        "cpa.media.execute": "cpa.media",
        "cpa.graph.project": "cpa.graph",
        "cpa.graph.project_entity_links": "cpa.graph",
        "cpa.pipeline.match": PIPELINE_QUEUE,
        "cpa.pipeline.resume": PIPELINE_QUEUE,
        "cpa.pipeline.portrait": PIPELINE_QUEUE,
        "cpa.pipeline.diagnostic": PIPELINE_QUEUE,
        "cpa.agent.run": AGENT_QUEUE,
        "cpa.agent.replay": AGENT_QUEUE,
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
def test_agent_worker_fails_closed_without_account_binding_in_disposable_mysql(
    tmp_path, monkeypatch,
):
    """Run the real cpa.agent task and verify invalid identity stops before LLM/tools."""
    import pymysql
    import redis

    broker = os.getenv(
        "CPA_TEST_AGENT_CELERY_BROKER_URL", "redis://127.0.0.1:16379/12",
    )
    monkeypatch.setenv("CPA_MEDIA_BROKER_URL", broker)
    monkeypatch.setenv("CPA_AGENT_QUEUE", "cpa.agent")

    from app.config import Config
    from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
    from app.services.agent_runtime.agent_run_jobs import dispatch_agent_run
    from app.services.agent_runtime.agent_run_store import AgentRunRepository
    from app.services.agent_runtime.media.jobs import AGENT_QUEUE, celery_app
    from app.services.db.mysql_client import MySQLClient
    from celery import Celery

    redis_client = redis.Redis.from_url(
        broker, socket_connect_timeout=2, socket_timeout=2,
    )
    assert redis_client.ping() and redis_client.dbsize() == 0, \
        "Agent transport probe Redis DB must be empty and dedicated"

    unique = uuid.uuid4().hex[:12]
    database = f"cpa_agent_worker_{unique}"
    node = f"cpa-agent-probe-{unique}@localhost"
    admin = pymysql.connect(host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
                            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
                            autocommit=True)
    mysql = worker = worker_log = None
    original_broker_url = celery_app.conf.broker_url
    worker_log_path = tmp_path / "agent-worker.log"
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=BACKEND, check=True, capture_output=True, text=True,
        )
        mysql = MySQLClient(
            host=Config.MYSQL_HOST,
            port=Config.MYSQL_PORT,
            user=Config.MYSQL_USER,
            password=Config.MYSQL_PASSWORD,
            database=database,
            pool_size=1,
            max_overflow=0,
        )
        repository = AgentRunRepository(mysql)
        submitted = repository.submit(
            student_id=f"worker_probe_{unique}",
            session_id=f"session_{unique}",
            idempotency_key=f"worker-probe-{unique}",
            request_payload={"message": "运输链路只读探针", "interaction_mode": "agent"},
        )
        run_id = submitted["run"]["run_id"]
        dispatch_store = AgentRunDispatchStore(repository)

        env = os.environ.copy()
        env.update({
            "CPA_MEDIA_BROKER_URL": broker,
            "CPA_AGENT_QUEUE": AGENT_QUEUE,
            "MYSQL_HOST": str(Config.MYSQL_HOST),
            "MYSQL_PORT": str(Config.MYSQL_PORT),
            "MYSQL_USER": str(Config.MYSQL_USER),
            "MYSQL_PASSWORD": str(Config.MYSQL_PASSWORD),
            "MYSQL_DATABASE": database,
            "CPA_CELERY_PROBE_DATABASE": database,
            "CPA_CELERY_PROBE_QUEUE": AGENT_QUEUE,
            "CPA_CELERY_PROBE_MARKER": os.devnull,
            "PYTHONPATH": os.pathsep.join(
                (str(TESTS), str(BACKEND), env.get("PYTHONPATH", "")),
            ),
            "PYTHONUTF8": "1",
        })
        command = [
            sys.executable, "-B", "-m", "celery",
            "-A", "celery_transport_probe:celery_app",
        ]
        worker_log = worker_log_path.open("wb")
        worker = subprocess.Popen(
            command + ["worker", "--pool=solo", "--concurrency=1",
                       f"--queues={AGENT_QUEUE}", f"--hostname={node}",
                       "--loglevel=INFO"],
            cwd=BACKEND, env=env, stdout=worker_log, stderr=subprocess.STDOUT,
        )
        control_app = Celery("cpa_agent_probe_control", broker=broker)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(f"Agent worker exited early with code {worker.returncode}")
            response = control_app.control.inspect(
                destination=[node], timeout=2,
            ).registered() or {}
            if node in response:
                assert "cpa.agent.run" in {
                    task.split("[")[0] for task in response[node]
                }
                active_queues = control_app.control.inspect(
                    destination=[node], timeout=2,
                ).active_queues() or {}
                assert any(
                    queue.get("name") == AGENT_QUEUE
                    for queue in active_queues.get(node, [])
                )
                break
            time.sleep(1)
        else:
            pytest.fail("Agent worker did not become inspectable")

        celery_app.conf.broker_url = broker
        # The task may have been imported before this isolated test selected
        # its broker. Check only non-secret connection identity fields; do not
        # force-close Kombu's process-global pool, which other tests may share.
        producer_connection = celery_app.connection_for_write()
        try:
            assert producer_connection.hostname == "127.0.0.1"
        finally:
            producer_connection.release()
        published = dispatch_agent_run(
            run_id, repository=repository, dispatch_store=dispatch_store,
        )
        assert published in {"published", "claimed", "terminal"}
        deadline = time.monotonic() + 45
        state = None
        run = None
        while time.monotonic() < deadline:
            state = dispatch_store.get_for_owner(
                run_id, student_id=f"worker_probe_{unique}",
            )
            run = repository.get_for_owner(run_id, student_id=f"worker_probe_{unique}")
            if run["status"] == "failed" and state["status"] == "terminal":
                break
            if worker.poll() is not None:
                pytest.fail(f"Agent worker exited with code {worker.returncode}")
            time.sleep(0.5)

        if (state is None or state["status"] != "terminal" or run is None
                or run["status"] != "failed"):
            if worker_log is not None:
                worker_log.flush()
            queue_length = redis_client.llen(AGENT_QUEUE)
            worker_output = worker_log_path.read_text(encoding="utf-8", errors="replace")
            pytest.fail(
                f"worker did not settle the invalid auth binding: status={state}, run={run}, "
                f"queue_length={queue_length}, worker_log=\n{worker_output}"
            )
        assert state["claim_attempts"] == 1
        assert run["status"] == "failed"
        assert run["current_plan_version"] == 0
        assert run["last_event_seq"] == 4
        assert run["current_plan"] is None
        assert "已停用或授权版本已变化" in run["final_result"]["reply"]
        assert repository.read_events(
            run_id, student_id=f"worker_probe_{unique}", limit=10,
        )["events"][-1]["event_type"] == "run.failed"
    finally:
        _stop(worker)
        if worker_log is not None:
            worker_log.close()
        if mysql is not None:
            mysql.close()
        celery_app.conf.broker_url = original_broker_url
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        for key in redis_client.scan_iter(match=f"*{unique}*"):
            redis_client.delete(key)
        # DB 12 must have been empty before this test; remove only queue/control
        # keys created by this isolated probe, never a shared logical DB.
        redis_client.delete(
            AGENT_QUEUE,
            f"_kombu.binding.{AGENT_QUEUE}",
            "_kombu.binding.celeryev",
            "_kombu.binding.celery.pidbox",
        )


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_CELERY") != "1" or
                    os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_CELERY=1 and CPA_TEST_LIVE_MYSQL=1")
def test_agent_executor_completes_read_only_http_run_in_disposable_services(
    tmp_path, monkeypatch,
):
    """Exercise HTTP → MySQL → Redis/Celery → real model → MySQL settlement.

    The session lookup and authentication principal are narrow test adapters;
    account state, run/plan/step/event persistence, worker claim, checkpoint
    store and provider calls use the disposable MySQL schema and real services.
    """
    from types import SimpleNamespace

    import pymysql
    import redis
    from celery import Celery
    from flask import Flask, g, request

    from app.api import agent_bp, agent_runs
    from app.api._helpers import Principal
    from app.config import Config
    from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
    from app.services.agent_runtime.agent_run_store import AgentRunRepository
    from app.services.agent_runtime.media.jobs import AGENT_QUEUE, celery_app
    from app.services.db.mysql_client import MySQLClient

    broker = os.getenv(
        "CPA_TEST_AGENT_EXECUTOR_BROKER_URL", "redis://127.0.0.1:16379/12",
    )
    redis_client = redis.Redis.from_url(
        broker, socket_connect_timeout=2, socket_timeout=2,
    )
    assert redis_client.ping() and redis_client.dbsize() == 0, \
        "Agent executor probe Redis DB must be empty and dedicated"

    unique = uuid.uuid4().hex[:12]
    capability_id = os.getenv(
        "CPA_TEST_AGENT_EXECUTOR_CAPABILITY_ID", "career-planning",
    ).strip()
    if capability_id not in {"career-planning", "education-screening"}:
        pytest.fail("CPA_TEST_AGENT_EXECUTOR_CAPABILITY_ID is not an allowed test capability")
    education_screening_probe = capability_id == "education-screening"
    database = f"cpa_agent_exec_{unique}"
    owner = f"agent_exec_{unique}"
    session_id = f"session_{unique}"
    node = f"cpa-agent-exec-{unique}@localhost"
    admin = pymysql.connect(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
        autocommit=True,
    )
    mysql = MySQLClient(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
        database=database, pool_size=2, max_overflow=0,
    )
    worker = worker_log = None
    original_broker_url = celery_app.conf.broker_url
    worker_log_path = tmp_path / "agent-executor-worker.log"
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=BACKEND, check=True, capture_output=True, text=True,
        )
        mysql.execute(
            "INSERT INTO user_account (id, username, password_hash, name, role, status, auth_epoch) "
            "VALUES (:id, :username, 'test-only-unused-hash', '合成测试账号', 'student', 'active', 0)",
            {"id": owner, "username": owner}, commit=True,
        )
        # This owner-bound L0 fixture is synthetic and lives only in the
        # disposable schema. Avoid confirm_resume_candidate() here: its normal
        # confirmation path also schedules portrait/diagnostic/graph jobs.
        confirmation_task_id = f"resume_confirmed_{unique}"
        candidate_profile = {
            "name": "合成测试同学", "school": "北京大学", "degree": "本科",
            "major": "计算机科学与技术", "skills": ["Python", "SQL"],
        }
        confirmed_profile = {
            **candidate_profile,
            "_resume_confirmation": {
                "task_id": confirmation_task_id,
                "student_id": owner,
                "source_evidence": {"fixture": "synthetic-only", "page_count": 1},
            },
        }
        confirmation_result = {
            "confirmed": True,
            "candidate_profile": candidate_profile,
            "structure_validation": {"ok": True},
            "source_evidence": {"fixture": "synthetic-only", "page_count": 1},
        }
        mysql.execute(
            "INSERT INTO student_profile "
            "(student_id,school,degree,major,profile_json,created_at,updated_at) "
            "VALUES (:sid,:school,:degree,:major,:profile,NOW(),NOW())",
            {
                "sid": owner, "school": candidate_profile["school"],
                "degree": candidate_profile["degree"], "major": candidate_profile["major"],
                "profile": json.dumps(confirmed_profile, ensure_ascii=False),
            }, commit=True,
        )
        mysql.execute(
            "INSERT INTO pipeline_task "
            "(task_id,pipeline,status,progress,result_json,student_id,created_at,updated_at) "
            "VALUES (:tid,'L0_parse_resume','completed',100,:result,:sid,NOW(),NOW())",
            {"tid": confirmation_task_id, "sid": owner,
             "result": json.dumps(confirmation_result, ensure_ascii=False)}, commit=True,
        )
        mysql.execute(
            "INSERT INTO student_portrait_4d "
            "(student_id,d1_score,d2_score,d3_score,d4_score,competitiveness,"
            "completeness,portrait_json,created_at,updated_at) "
            "VALUES (:sid,37,0,0,0,9.25,0.2,:portrait,NOW(),NOW())",
            {"sid": owner, "portrait": json.dumps({
                "D1_basic": {"sub_school": 37, "dimension_score": 37},
                "status": "partial",
                "source_confirmation_task_id": confirmation_task_id,
            }, ensure_ascii=False)}, commit=True,
        )

        def d1_business_snapshot():
            return {
                "student_profile": mysql.fetch_all(
                    "SELECT * FROM student_profile WHERE student_id=:sid",
                    {"sid": owner},
                ),
                "student_portrait_4d": mysql.fetch_all(
                    "SELECT * FROM student_portrait_4d WHERE student_id=:sid ORDER BY id",
                    {"sid": owner},
                ),
                "student_portrait_snapshot": mysql.fetch_all(
                    "SELECT * FROM student_portrait_snapshot WHERE student_id=:sid "
                    "ORDER BY snapshot_id",
                    {"sid": owner},
                ),
                "school_tier_review": mysql.fetch_all(
                    "SELECT * FROM school_tier_review WHERE student_id=:sid ORDER BY id",
                    {"sid": owner},
                ),
            }

        d1_before = d1_business_snapshot()
        repository = AgentRunRepository(mysql)
        dispatch_store = AgentRunDispatchStore(repository)

        # Use a test authentication adapter, but bind it to the real isolated
        # account row and auth_epoch that the worker must revalidate.
        monkeypatch.setattr(Config, "DEMO_MODE", False)
        db_module = __import__("app.services.db.mysql_client", fromlist=["_mysql_client"])
        monkeypatch.setattr(db_module, "_mysql_client", mysql)
        monkeypatch.setattr(agent_runs, "get_agent_run_repository", lambda: repository)

        class SessionOwnerAdapter:
            def refresh_session(self, requested, *, require_authoritative=False):
                assert require_authoritative is True
                if requested != session_id:
                    return None
                return SimpleNamespace(session_id=session_id, student_id=owner)

        monkeypatch.setattr(
            agent_runs, "get_session_manager", lambda: SessionOwnerAdapter(),
        )

        env = os.environ.copy()
        env.update({
            "CPA_MEDIA_BROKER_URL": broker,
            "CPA_AGENT_QUEUE": AGENT_QUEUE,
            "MYSQL_HOST": str(Config.MYSQL_HOST),
            "MYSQL_PORT": str(Config.MYSQL_PORT),
            "MYSQL_USER": str(Config.MYSQL_USER),
            "MYSQL_PASSWORD": str(Config.MYSQL_PASSWORD),
            "MYSQL_DATABASE": database,
            "CPA_CELERY_PROBE_DATABASE": database,
            "CPA_CELERY_PROBE_QUEUE": AGENT_QUEUE,
            "CPA_CELERY_PROBE_MARKER": os.devnull,
            "PYTHONPATH": os.pathsep.join(
                (str(TESTS), str(BACKEND), env.get("PYTHONPATH", "")),
            ),
            "PYTHONUTF8": "1",
        })
        command = [
            sys.executable, "-B", "-m", "celery",
            "-A", "celery_transport_probe:celery_app",
        ]
        worker_log = worker_log_path.open("wb")
        worker = subprocess.Popen(
            command + ["worker", "--pool=solo", "--concurrency=1",
                       f"--queues={AGENT_QUEUE}", f"--hostname={node}",
                       "--loglevel=INFO"],
            cwd=BACKEND, env=env, stdout=worker_log, stderr=subprocess.STDOUT,
        )
        control_app = Celery("cpa_agent_executor_control", broker=broker)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(f"Agent executor worker exited early with code {worker.returncode}")
            inspect = control_app.control.inspect(destination=[node], timeout=2)
            registered = inspect.registered() or {}
            active_queues = inspect.active_queues() or {}
            if node in registered and node in active_queues:
                assert "cpa.agent.run" in {
                    task.split("[")[0] for task in registered[node]
                }
                assert any(queue.get("name") == AGENT_QUEUE
                           for queue in active_queues[node])
                break
            time.sleep(1)
        else:
            pytest.fail("Agent executor worker did not become inspectable")

        celery_app.conf.broker_url = broker
        connection = celery_app.connection_for_write()
        try:
            assert connection.hostname == "127.0.0.1"
        finally:
            connection.release()

        app = Flask("cpa_agent_executor_live_probe")
        app.register_blueprint(agent_bp, url_prefix="/api/agent")

        @app.before_request
        def inject_account_principal():
            g.cpa_principal = Principal(
                principal_id=owner, student_id=owner, source="account",
            )

        client = app.test_client()
        message = (
            "请只读分析：一则岗位公告原文是“硕士及以上”；我仅有本科学历，"
            "已确认简历中的毕业院校是北京大学。请按我提供的要求和自述做局部学历比较，"
            "并结合可读的已确认院校资料说明北京大学是否与历史 211 名单精确匹配、名单年份；"
            "说明这些事实不能证明什么，并给一个低成本下一步。不要推断整体资格、985/双一流或概率，"
            "不要修改 D1、画像或任何正式业务资料。"
            if education_screening_probe else
            "请根据我已确认简历中的毕业院校“北京大学”，只读回答：这个精确校名是否命中"
            "已读取的历史 211 名单？说明名单年份，以及该事实不能证明什么；再给一句求职语境建议。"
            "不要修改 D1、画像或任何正式业务资料。"
        )
        response = client.post(
            "/api/agent/runs",
            headers={"Idempotency-Key": "live-agent-" + unique},
            json={"session_id": session_id, "message": message,
                  "interaction_mode": "experts", "expert_id": "career-coach",
                  "capability_id": capability_id},
        )
        assert response.status_code == 201, response.get_json()
        run_id = response.get_json()["data"]["run"]["run_id"]

        deadline = time.monotonic() + 300
        run = None
        dispatch = None
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(f"Agent executor worker exited with code {worker.returncode}")
            run = repository.get_for_owner(run_id, student_id=owner)
            dispatch = dispatch_store.get_for_owner(run_id, student_id=owner)
            if run["status"] in {"completed", "failed", "cancelled", "needs_reconciliation"}:
                break
            time.sleep(1)
        if run is not None:
            status_response = client.get(f"/api/agent/runs/{run_id}")
            events_response = client.get(
                f"/api/agent/runs/{run_id}/events?after_seq=0&limit=200",
            )
            assert status_response.status_code == 200, status_response.get_json()
            assert events_response.status_code == 200, events_response.get_json()
            events = repository.read_events(
                run_id, student_id=owner, limit=200,
            )["events"]
            sql_run = mysql.fetch_one(
                "SELECT run_id, student_id, status, current_plan_version, "
                "last_event_seq, final_result_json, final_result_sha256 "
                "FROM agent_run WHERE run_id=:run_id",
                {"run_id": run_id},
            )
            sql_plans = mysql.fetch_all(
                "SELECT plan_version, goal_summary, plan_json, plan_sha256 "
                "FROM agent_run_plan WHERE run_id=:run_id ORDER BY plan_version",
                {"run_id": run_id},
            )
            sql_steps = mysql.fetch_all(
                "SELECT plan_version, step_index, role, goal, status, result_json, "
                "result_sha256, error_code FROM agent_run_step WHERE run_id=:run_id "
                "ORDER BY plan_version, step_index",
                {"run_id": run_id},
            )
            sql_events = mysql.fetch_all(
                "SELECT event_seq, event_type, run_status, payload_json "
                "FROM agent_run_event WHERE run_id=:run_id ORDER BY event_seq",
                {"run_id": run_id},
            )
            d1_after = d1_business_snapshot()
            from app.services.agent_runtime.school_facts import lookup_historical_211

            unknown_school_fact = lookup_historical_211(
                "合成未知院校-" + unique,
            )
            worker_log.flush()
            log_text = worker_log_path.read_text(encoding="utf-8", errors="replace")
            model_usage_lines = [
                line.strip() for line in log_text.splitlines()
                if "LLM OK provider=" in line or "fork job usage kind=" in line
            ]
            evidence = {
                "scope": "synthetic disposable MySQL/Redis/Celery test only",
                "http_submit_status": response.status_code,
                "http_submit_dispatch_status": response.get_json()["data"]["dispatch_status"],
                "http_status_readback": status_response.get_json(),
                "http_events_readback": events_response.get_json(),
                "message": message,
                "run_id": run_id,
                "owner": owner,
                "mysql_schema": database,
                "dispatch": dispatch,
                "repository_run": run,
                "mysql_run": sql_run,
                "mysql_plans": sql_plans,
                "mysql_steps": sql_steps,
                "mysql_events": sql_events,
                "repository_events": events,
                "model_usage_log_lines": model_usage_lines,
                "d1_snapshot_before": d1_before,
                "d1_snapshot_after": d1_after,
                "unknown_school_fact": unknown_school_fact,
            }
            (tmp_path / "agent-executor-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            # Windows PowerShell/pytest can route stdout through a legacy GBK
            # code page. Keep the evidence file UTF-8 but make console output
            # ASCII-safe so output encoding cannot fail after a successful run.
            print(json.dumps(evidence, ensure_ascii=True, sort_keys=True, default=str))
        assert run is not None and run["status"] == "completed", {
            "status": run["status"] if run else None,
            "evidence_path": str(tmp_path / "agent-executor-evidence.json"),
            "worker_log_path": str(worker_log_path),
        }
        plan = run["current_plan"]
        assert plan and plan["steps"]
        assert len(plan["steps"]) == 1
        step = plan["steps"][0]
        assert step["role"] == "qa" and step["status"] == "completed"
        expected_skill_ids = (
            ["education-screening-context", "school-context"]
            if education_screening_probe else ["school-context"]
        )
        assert step["skill_ids"] == expected_skill_ids
        lead_decision = plan["metadata"]["lead_decision"]
        assert lead_decision["action"] == "run_plan"
        assert lead_decision["capability_fit"] == "supported"
        assert len(lead_decision["steps"]) == 1
        assert lead_decision["steps"][0]["role"] == "qa"
        assert lead_decision["steps"][0]["skill_ids"] == expected_skill_ids
        assert "reply" not in lead_decision
        binding = plan["metadata"]["capability_binding"]
        assert binding["expert_id"] == "career-coach"
        assert binding["capability_id"] == capability_id
        assert binding["version"] == (
            "0.1.0" if education_screening_probe else "1.0.0"
        )
        release = plan["metadata"]["runtime_release"]
        if education_screening_probe:
            education_release = release["education_pipeline"]
            assert education_release["contract_version"] == 1
            assert len(education_release["extractor"]["prompt_sha256"]) == 64
            assert len(education_release["finalizer"]["schema_sha256"]) == 64
            assert sql_plans[0]["plan_json"]["metadata"]["release_sha256"] == release["release_sha256"]
            assert step["result"]["education_pipeline_release_sha256"]
            assert step["result"]["education_finalizer"]["pipeline_release_sha256"]
            assert step["result"]["education_conditions"]["status"] == "model_candidate_unverified"
            assert len(step["result"]["education_conditions"]["conditions"]) >= 2
            assert step["result"]["model_usage"]["status"] == "observed"
            assert step["result"]["model_usage"]["call_count"] >= 3
            assert step["result"]["verified_updates"] == {}
            final_reply = run["final_result"]["reply"]
            assert "学历这一项暂不满足" in final_reply
            assert "北京大学" in final_reply and "211" in final_reply and "2005" in final_reply
            assert not any(token in final_reply for token in ("概率", "ATS", "录取率", "D1评分"))
        else:
            assert "education_pipeline" not in release
        school_release = release["skill_sources"]["school-context"]
        assert release["capability_binding"] == binding
        assert school_release["profile_status"] == "matched_official_name"
        assert school_release["profile_version"] == confirmation_task_id
        assert len(school_release["knowledge_sha256"]) == 64
        skill_version = next(
            item for item in step["result"]["skill_versions"]
            if item["skill_id"] == "school-context"
        )
        assert skill_version["skill_id"] == "school-context"
        assert skill_version["profile_version"] == confirmation_task_id
        assert skill_version["knowledge_sha256"] == school_release["knowledge_sha256"]
        assert any(
            call.get("tool_name") == "school_context_source_read"
            and call.get("status") == "done"
            for call in step["result"]["tool_calls"]
        )
        assert d1_after == d1_before
        assert unknown_school_fact["state"] == "unknown"
        assert run["final_result"] and run["final_result"].get("reply")
        assert status_response.get_json()["data"]["run"]["status"] == "completed"
        assert sql_run["status"] == "completed"
        events = repository.read_events(run_id, student_id=owner, limit=200)["events"]
        assert [event["event_seq"] for event in events] == list(
            range(1, run["last_event_seq"] + 1)
        )

        # A duplicate transport delivery after settlement must not call the
        # model, append another run event, or overwrite the authoritative result.
        usage_count = len(model_usage_lines)
        before_duplicate_seq = run["last_event_seq"]
        before_duplicate_result = run["final_result"]
        duplicate_task_id = "duplicate-probe-" + unique
        from app.services.agent_runtime.agent_run_jobs import agent_run_task

        agent_run_task.apply_async(
            args=[run_id], task_id=duplicate_task_id, queue=AGENT_QUEUE, retry=False,
        )
        duplicate_deadline = time.monotonic() + 40
        while time.monotonic() < duplicate_deadline:
            worker_log.flush()
            current_log = worker_log_path.read_text(encoding="utf-8", errors="replace")
            if f"Task cpa.agent.run[{duplicate_task_id}] succeeded" in current_log:
                break
            time.sleep(0.5)
        else:
            pytest.fail("duplicate delivery was not acknowledged by the real worker")
        duplicate_run = repository.get_for_owner(run_id, student_id=owner)
        worker_log.flush()
        after_duplicate_log = worker_log_path.read_text(encoding="utf-8", errors="replace")
        after_duplicate_usage = [
            line for line in after_duplicate_log.splitlines()
            if "LLM OK provider=" in line or "fork job usage kind=" in line
        ]
        assert duplicate_run["status"] == "completed"
        assert duplicate_run["last_event_seq"] == before_duplicate_seq
        assert duplicate_run["final_result"] == before_duplicate_result
        assert len(after_duplicate_usage) == usage_count

        # Cancellation before any claim is effective at a safe point. The
        # transport message is intentionally left in isolated Redis, then a
        # restarted worker must acknowledge it without starting the executor.
        _stop(worker)
        worker = None
        worker_log.close()
        worker_log = None
        cancel_key = "live-agent-cancel-" + unique
        cancel_submit = client.post(
            "/api/agent/runs",
            headers={"Idempotency-Key": cancel_key},
            json={"session_id": session_id,
                  "message": "请只读回答：当前请求是否启动了执行？不要调用工具。",
                  "interaction_mode": "agent"},
        )
        assert cancel_submit.status_code == 201, cancel_submit.get_json()
        cancel_run_id = cancel_submit.get_json()["data"]["run"]["run_id"]
        assert cancel_submit.get_json()["data"]["dispatch_status"] in {
            "published", "publishing_unverified",
        }
        cancel_response = client.post(f"/api/agent/runs/{cancel_run_id}/cancel")
        assert cancel_response.status_code == 200, cancel_response.get_json()
        cancelled = repository.get_for_owner(cancel_run_id, student_id=owner)
        cancelled_dispatch = dispatch_store.get_for_owner(
            cancel_run_id, student_id=owner,
        )
        cancelled_events = repository.read_events(
            cancel_run_id, student_id=owner, limit=200,
        )["events"]
        assert cancelled["status"] == "cancelled"
        assert cancelled_dispatch["status"] == "cancelled"
        cancel_last_seq = cancelled["last_event_seq"]

        worker_log = worker_log_path.open("ab")
        worker = subprocess.Popen(
            command + ["worker", "--pool=solo", "--concurrency=1",
                       f"--queues={AGENT_QUEUE}", f"--hostname={node}",
                       "--loglevel=INFO"],
            cwd=BACKEND, env=env, stdout=worker_log, stderr=subprocess.STDOUT,
        )
        cancel_task_id = "cpa-agent-" + cancel_run_id.removeprefix("run_")
        cancel_deadline = time.monotonic() + 60
        while time.monotonic() < cancel_deadline:
            if worker.poll() is not None:
                pytest.fail(f"restarted worker exited with code {worker.returncode}")
            worker_log.flush()
            current_log = worker_log_path.read_text(encoding="utf-8", errors="replace")
            if f"Task cpa.agent.run[{cancel_task_id}] succeeded" in current_log:
                break
            time.sleep(0.5)
        else:
            pytest.fail("cancelled delivery was not acknowledged after worker restart")
        after_cancel_delivery = repository.get_for_owner(
            cancel_run_id, student_id=owner,
        )
        after_cancel_events = repository.read_events(
            cancel_run_id, student_id=owner, limit=200,
        )["events"]
        assert after_cancel_delivery["status"] == "cancelled"
        assert after_cancel_delivery["last_event_seq"] == cancel_last_seq
        assert after_cancel_events == cancelled_events
        worker_log.flush()
        final_log = worker_log_path.read_text(encoding="utf-8", errors="replace")
        final_usage_lines = [
            line.strip() for line in final_log.splitlines()
            if "LLM OK provider=" in line or "fork job usage kind=" in line
        ]
        assert len(final_usage_lines) == usage_count
        (tmp_path / "agent-executor-safety-evidence.json").write_text(
            json.dumps({
                "duplicate_delivery": {
                    "task_id": duplicate_task_id,
                    "before_event_seq": before_duplicate_seq,
                    "after_event_seq": duplicate_run["last_event_seq"],
                    "model_usage_count_before_after": [usage_count, len(after_duplicate_usage)],
                    "final_result_unchanged": duplicate_run["final_result"] == before_duplicate_result,
                },
                "cancel_before_claim": {
                    "run_id": cancel_run_id,
                    "http_submit_status": cancel_submit.status_code,
                    "http_cancel_status": cancel_response.status_code,
                    "status_before_delivery": cancelled["status"],
                    "dispatch_before_delivery": cancelled_dispatch["status"],
                    "status_after_worker_restart": after_cancel_delivery["status"],
                    "events_unchanged_after_delivery": after_cancel_events == cancelled_events,
                    "model_usage_count_before_after": [usage_count, len(final_usage_lines)],
                },
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Emit a redacted, synthetic-only evidence record for the live guide.
        print(json.dumps({
            "http_status": response.status_code,
            "initial_dispatch_status": response.get_json()["data"]["dispatch_status"],
            "capability_id": capability_id,
            "run_id": run_id,
            "owner": owner,
            "worker_queue": AGENT_QUEUE,
            "mysql_schema": database,
            "final_status": run["status"],
            "plan_version": plan["plan_version"],
            "goal_summary": plan["goal_summary"],
            "steps": [{
                "step_index": step["step_index"], "role": step["role"],
                "status": step["status"], "skill_ids": step["skill_ids"],
                "result": step["result"],
            } for step in plan["steps"]],
            "events": [{
                "seq": event["event_seq"], "type": event["event_type"],
                "status": event["run_status"],
            } for event in events],
            "final_reply": run["final_result"]["reply"],
        }, ensure_ascii=True, sort_keys=True))
    finally:
        _stop(worker)
        if worker_log is not None:
            worker_log.close()
        mysql.close()
        celery_app.conf.broker_url = original_broker_url
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        for key in redis_client.scan_iter(match=f"*{unique}*"):
            redis_client.delete(key)
        redis_client.delete(
            AGENT_QUEUE,
            f"_kombu.binding.{AGENT_QUEUE}",
            "_kombu.binding.celeryev",
            "_kombu.binding.celery.pidbox",
        )


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_CELERY") != "1" or
                    os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_CELERY=1 and CPA_TEST_LIVE_MYSQL=1")
def test_agent_worker_duplicate_and_cancel_before_claim_safe_boundary(
    tmp_path, monkeypatch,
):
    """Verify duplicate terminal delivery and cancel-before-claim without a model."""
    from types import SimpleNamespace

    import pymysql
    import redis
    from celery import Celery
    from flask import Flask, g

    from app.api import agent_bp, agent_runs
    from app.api._helpers import Principal
    from app.config import Config
    from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
    from app.services.agent_runtime.agent_run_store import AgentRunRepository
    from app.services.agent_runtime.media.jobs import AGENT_QUEUE, celery_app
    from app.services.db.mysql_client import MySQLClient

    broker = os.getenv(
        "CPA_TEST_AGENT_EXECUTOR_SAFETY_BROKER_URL", "redis://127.0.0.1:16379/13",
    )
    redis_client = redis.Redis.from_url(
        broker, socket_connect_timeout=2, socket_timeout=2,
    )
    assert redis_client.ping() and redis_client.dbsize() == 0, \
        "Agent safety probe Redis DB must be empty and dedicated"

    unique = uuid.uuid4().hex[:12]
    database = f"cpa_agent_safe_{unique}"
    owner = f"agent_safe_{unique}"
    session_id = f"session_{unique}"
    node = f"cpa-agent-safe-{unique}@localhost"
    admin = pymysql.connect(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
        autocommit=True,
    )
    mysql = MySQLClient(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
        database=database, pool_size=2, max_overflow=0,
    )
    worker = worker_log = None
    original_broker_url = celery_app.conf.broker_url
    worker_log_path = tmp_path / "agent-safety-worker.log"
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=BACKEND, check=True, capture_output=True, text=True,
        )
        mysql.execute(
            "INSERT INTO user_account (id, username, password_hash, role, status, auth_epoch) "
            "VALUES (:id, :username, 'test-only-unused-hash', 'student', 'active', 0)",
            {"id": owner, "username": owner}, commit=True,
        )
        repository = AgentRunRepository(mysql)
        dispatch_store = AgentRunDispatchStore(repository)
        monkeypatch.setattr(Config, "DEMO_MODE", False)
        db_module = __import__("app.services.db.mysql_client", fromlist=["_mysql_client"])
        monkeypatch.setattr(db_module, "_mysql_client", mysql)
        monkeypatch.setattr(agent_runs, "get_agent_run_repository", lambda: repository)

        class SessionOwnerAdapter:
            def refresh_session(self, requested, *, require_authoritative=False):
                assert require_authoritative is True
                if requested != session_id:
                    return None
                return SimpleNamespace(session_id=session_id, student_id=owner)

        monkeypatch.setattr(
            agent_runs, "get_session_manager", lambda: SessionOwnerAdapter(),
        )
        app = Flask("cpa_agent_executor_safety_probe")
        app.register_blueprint(agent_bp, url_prefix="/api/agent")

        @app.before_request
        def inject_account_principal():
            g.cpa_principal = Principal(
                principal_id=owner, student_id=owner, source="account",
            )

        client = app.test_client()
        env = os.environ.copy()
        env.update({
            "CPA_MEDIA_BROKER_URL": broker,
            "CPA_AGENT_QUEUE": AGENT_QUEUE,
            "MYSQL_HOST": str(Config.MYSQL_HOST),
            "MYSQL_PORT": str(Config.MYSQL_PORT),
            "MYSQL_USER": str(Config.MYSQL_USER),
            "MYSQL_PASSWORD": str(Config.MYSQL_PASSWORD),
            "MYSQL_DATABASE": database,
            "CPA_CELERY_PROBE_DATABASE": database,
            "CPA_CELERY_PROBE_QUEUE": AGENT_QUEUE,
            "CPA_CELERY_PROBE_MARKER": os.devnull,
            "PYTHONPATH": os.pathsep.join(
                (str(TESTS), str(BACKEND), env.get("PYTHONPATH", "")),
            ),
            "PYTHONUTF8": "1",
        })
        command = [
            sys.executable, "-B", "-m", "celery",
            "-A", "celery_transport_probe:celery_app",
        ]

        # Seed a fully settled owner-bound run directly in MySQL. This lets the
        # real Celery worker consume a duplicate without invoking any model.
        seeded = repository.submit(
            student_id=owner,
            session_id=session_id,
            idempotency_key="seed-completed-" + unique,
            request_payload={"message": "已完成的安全边界样本", "interaction_mode": "agent"},
            principal_id=owner,
            principal_source="account",
            principal_auth_epoch=0,
        )
        completed_id = seeded["run"]["run_id"]
        repository.begin_planning(completed_id, student_id=owner)
        plan = repository.persist_plan(
            completed_id,
            student_id=owner,
            expected_plan_version=0,
            goal_summary="预置一个已完成的只读任务用于重复派发安全验证",
            steps=[{"role": "qa", "goal": "只读确认已完成状态", "skill_ids": []}],
        )
        repository.start_step(
            completed_id, student_id=owner,
            plan_version=plan["plan_version"], step_index=1,
        )
        repository.finish_step(
            completed_id,
            student_id=owner,
            plan_version=plan["plan_version"],
            step_index=1,
            outcome="completed",
            result={"verified_facts": [], "reply_candidate_unverified": "测试预置结果"},
        )
        repository.begin_finalizing(completed_id, student_id=owner)
        completed_before = repository.complete_run(
            completed_id,
            student_id=owner,
            final_result={"reply": "这是仅供安全状态验证的预置结果。", "format": "text"},
        )
        duplicate_before_seq = completed_before["last_event_seq"]

        env["CPA_MEDIA_BROKER_URL"] = broker
        worker_log = worker_log_path.open("wb")
        worker = subprocess.Popen(
            command + ["worker", "--pool=solo", "--concurrency=1",
                       f"--queues={AGENT_QUEUE}", f"--hostname={node}",
                       "--loglevel=INFO"],
            cwd=BACKEND, env=env, stdout=worker_log, stderr=subprocess.STDOUT,
        )
        control_app = Celery("cpa_agent_safety_control", broker=broker)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(f"safety worker exited early with code {worker.returncode}")
            inspect = control_app.control.inspect(destination=[node], timeout=2)
            registered = inspect.registered() or {}
            active_queues = inspect.active_queues() or {}
            if node in registered and node in active_queues:
                break
            time.sleep(1)
        else:
            pytest.fail("safety worker did not become inspectable")

        celery_app.conf.broker_url = broker
        from app.services.agent_runtime.agent_run_jobs import agent_run_task

        duplicate_task_id = "safe-duplicate-" + unique
        agent_run_task.apply_async(
            args=[completed_id], task_id=duplicate_task_id,
            queue=AGENT_QUEUE, retry=False,
        )
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            worker_log.flush()
            log_text = worker_log_path.read_text(encoding="utf-8", errors="replace")
            if f"Task cpa.agent.run[{duplicate_task_id}] succeeded" in log_text:
                break
            time.sleep(0.5)
        else:
            pytest.fail("completed-run duplicate delivery was not acknowledged")
        completed_after = repository.get_for_owner(completed_id, student_id=owner)
        duplicate_dispatch = dispatch_store.get_for_owner(completed_id, student_id=owner)
        assert completed_after["status"] == "completed"
        assert completed_after["last_event_seq"] == duplicate_before_seq
        assert completed_after["final_result"] == completed_before["final_result"]
        assert duplicate_dispatch["status"] == "terminal"

        # Leave worker stopped while HTTP submit publishes, then cancel at the
        # pre-claim safe point. Restarted worker must acknowledge but not claim.
        _stop(worker)
        worker = None
        worker_log.close()
        worker_log = None
        cancel_submit = client.post(
            "/api/agent/runs",
            headers={"Idempotency-Key": "cancel-before-claim-" + unique},
            json={"session_id": session_id,
                  "message": "请只读回答当前任务是否已启动。",
                  "interaction_mode": "agent"},
        )
        assert cancel_submit.status_code == 201, cancel_submit.get_json()
        cancel_run_id = cancel_submit.get_json()["data"]["run"]["run_id"]
        assert cancel_submit.get_json()["data"]["dispatch_status"] == "published"
        cancel_response = client.post(f"/api/agent/runs/{cancel_run_id}/cancel")
        assert cancel_response.status_code == 200, cancel_response.get_json()
        cancelled_before = repository.get_for_owner(cancel_run_id, student_id=owner)
        cancelled_dispatch_before = dispatch_store.get_for_owner(
            cancel_run_id, student_id=owner,
        )
        events_before = repository.read_events(
            cancel_run_id, student_id=owner, limit=200,
        )["events"]
        assert cancelled_before["status"] == "cancelled"
        assert cancelled_dispatch_before["status"] == "cancelled"

        worker_log = worker_log_path.open("ab")
        worker = subprocess.Popen(
            command + ["worker", "--pool=solo", "--concurrency=1",
                       f"--queues={AGENT_QUEUE}", f"--hostname={node}",
                       "--loglevel=INFO"],
            cwd=BACKEND, env=env, stdout=worker_log, stderr=subprocess.STDOUT,
        )
        cancel_task_id = "cpa-agent-" + cancel_run_id.removeprefix("run_")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(f"restarted safety worker exited with code {worker.returncode}")
            worker_log.flush()
            log_text = worker_log_path.read_text(encoding="utf-8", errors="replace")
            if f"Task cpa.agent.run[{cancel_task_id}] succeeded" in log_text:
                break
            time.sleep(0.5)
        else:
            pytest.fail("cancelled pre-claim message was not acknowledged after restart")
        cancelled_after = repository.get_for_owner(cancel_run_id, student_id=owner)
        events_after = repository.read_events(
            cancel_run_id, student_id=owner, limit=200,
        )["events"]
        worker_log.flush()
        final_log = worker_log_path.read_text(encoding="utf-8", errors="replace")
        assert cancelled_after["status"] == "cancelled"
        assert cancelled_after["last_event_seq"] == cancelled_before["last_event_seq"]
        assert events_after == events_before
        assert "LLM OK provider=" not in final_log
        evidence = {
            "scope": "isolated MySQL/Redis/real Celery worker; no model calls",
            "database": database,
            "owner": owner,
            "duplicate_delivery": {
                "task_id": duplicate_task_id,
                "status": completed_after["status"],
                "event_seq_before_after": [duplicate_before_seq, completed_after["last_event_seq"]],
                "final_result_unchanged": completed_after["final_result"] == completed_before["final_result"],
                "dispatch_after": duplicate_dispatch["status"],
            },
            "cancel_before_claim": {
                "run_id": cancel_run_id,
                "submit_http_status": cancel_submit.status_code,
                "cancel_http_status": cancel_response.status_code,
                "status_before_after_delivery": [cancelled_before["status"], cancelled_after["status"]],
                "dispatch_before_delivery": cancelled_dispatch_before["status"],
                "event_seq_before_after_delivery": [cancelled_before["last_event_seq"], cancelled_after["last_event_seq"]],
                "events_unchanged_after_delivery": events_after == events_before,
                "model_called": "LLM OK provider=" in final_log,
            },
        }
        evidence_path = tmp_path / "agent-executor-safety-evidence.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
    finally:
        _stop(worker)
        if worker_log is not None:
            worker_log.close()
        mysql.close()
        celery_app.conf.broker_url = original_broker_url
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()
        for key in redis_client.scan_iter(match=f"*{unique}*"):
            redis_client.delete(key)
        redis_client.delete(
            AGENT_QUEUE,
            f"_kombu.binding.{AGENT_QUEUE}",
            "_kombu.binding.celeryev",
            "_kombu.binding.celery.pidbox",
        )


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
                    reason="Set CPA_TEST_LIVE_MYSQL=1 for disposable real-MySQL tool-scope diagnostic")
def test_career_data_read_raw_and_gated_results_under_mysql_claim_scope(
    tmp_path, monkeypatch,
):
    """Inspect owner scope and raw/gated tool results without calling a model."""
    import pymysql
    from concurrent.futures import ThreadPoolExecutor

    from app.config import Config
    from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
    from app.services.agent_runtime.agent_run_store import AgentRunRepository
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.pre_tool_use import GateContext
    from app.services.db.mysql_client import MySQLClient
    from app.api._helpers import trusted_agent_worker_scope

    from app.services.agent_runtime.gated_tools import build_gated_tools

    unique = uuid.uuid4().hex[:12]
    database = f"cpa_agent_tool_{unique}"
    owner = f"agent_tool_{unique}"
    session_id = f"session_{unique}"
    admin = pymysql.connect(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
        autocommit=True,
    )
    mysql = MySQLClient(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
        database=database, pool_size=2, max_overflow=0,
    )
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "alembic.ini",
             "-x", f"database={database}", "upgrade", "head"],
            cwd=BACKEND, check=True, capture_output=True, text=True,
        )
        mysql.execute(
            "INSERT INTO user_account (id, username, password_hash, role, status, auth_epoch) "
            "VALUES (:id, :username, 'test-only-unused-hash', 'student', 'active', 0)",
            {"id": owner, "username": owner}, commit=True,
        )
        repository = AgentRunRepository(mysql)
        submitted = repository.submit(
            student_id=owner, session_id=session_id,
            idempotency_key="tool-scope-" + unique,
            request_payload={"message": "只读资源目录诊断", "interaction_mode": "agent"},
            principal_id=owner, principal_source="account", principal_auth_epoch=0,
        )
        run_id = submitted["run"]["run_id"]
        dispatch = AgentRunDispatchStore(repository)
        reservation = dispatch.reserve_publish(run_id)
        assert reservation["reserved"] is True
        assert dispatch.finish_publish(
            run_id, publish_token=reservation["publish_token"], published=True,
        ) == "published"
        claim = dispatch.claim_worker(run_id)
        assert claim is not None

        db_module = __import__("app.services.db.mysql_client", fromlist=["_mysql_client"])
        monkeypatch.setattr(db_module, "_mysql_client", mysql)
        from app.services.tools.career_data_read import CareerDataReadTool

        context = GateContext(
            interaction_mode=InteractionMode.AGENT,
            subagent="qa", session_id=session_id, student_id=owner,
            plan_tool_allowlist={"career_data_read"},
            allow_artifact_commit=False,
        )
        ui_cards = []

        def current_scope(rid, token):
            if not dispatch.claim_is_current(rid, token):
                return False
            binding = dispatch.get_claim_binding(rid, token)
            row = mysql.fetch_one(
                "SELECT status, auth_epoch FROM user_account WHERE id=:id LIMIT 1",
                {"id": owner},
            )
            return bool(
                binding and binding["student_id"] == owner
                and row and row["status"] == "active" and row["auth_epoch"] == 0
            )

        with trusted_agent_worker_scope(
            run_id=run_id, claim_token=claim["claim_token"],
            load_claim_binding=dispatch.get_claim_binding,
            scope_is_current=current_scope,
        ):
            raw_result = CareerDataReadTool().execute(operation="list")
            raw_read_result = CareerDataReadTool().execute(
                operation="read", uri="career://me/resume", section="overview",
            )
            tool = next(
                item for item in build_gated_tools("qa", context, ui_cards)
                if item.name == "career_data_read"
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                gated_result = executor.submit(
                    tool.invoke, {"operation": "list"},
                ).result(timeout=30)
                gated_read_result = executor.submit(
                    tool.invoke, {
                        "operation": "read", "uri": "career://me/resume",
                        "section": "overview",
                    },
                ).result(timeout=30)

        record = {
            "mysql_schema": database,
            "owner": owner,
            "run_id": run_id,
            "claim_attempt": dispatch.get_for_owner(run_id, student_id=owner)["claim_attempts"],
            "raw_result": json.loads(raw_result),
            "raw_read_result": json.loads(raw_read_result),
            "gated_result": gated_result,
            "gated_read_result": gated_read_result,
            "ui_cards": ui_cards,
        }
        evidence_path = tmp_path / "career-data-read-scope-evidence.json"
        evidence_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str))
        raw = record["raw_result"]
        assert raw.get("success") is True, record
        assert raw.get("status") in {"empty", "partial", "completed"}, record
        assert record["raw_read_result"].get("status") == "unavailable", record
        assert ui_cards and ui_cards[0].get("status") in {"done", "partial"}, record
        assert ui_cards[1].get("status") == "unavailable", record
        assert "未成功" not in ui_cards[1].get("summary", ""), record
    finally:
        mysql.close()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.close()


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
