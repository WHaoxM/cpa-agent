"""Owner-bound Agent run state transitions and cursor contract on SQLite."""

import pytest
from sqlalchemy import create_engine, event, text
from datetime import datetime, timedelta, timezone

from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
from app.services.agent_runtime.agent_run_store import (
    AgentRunConflict,
    AgentRunIdempotencyConflict,
    AgentRunNotFound,
    AgentRunRepository,
)


class SQLiteRunStoreDB:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        event.listen(
            self.engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE agent_run (
                    run_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    idempotency_key_sha256 TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    input_snapshot_version INTEGER NOT NULL,
                    input_snapshot_redaction_version INTEGER NOT NULL,
                    input_snapshot_bytes INTEGER NOT NULL,
                    input_snapshot_json TEXT NOT NULL,
                    input_snapshot_sha256 TEXT NOT NULL,
                    principal_id TEXT,
                    principal_source TEXT,
                    principal_auth_epoch INTEGER,
                    status TEXT NOT NULL,
                    current_plan_version INTEGER NOT NULL DEFAULT 0,
                    last_event_seq INTEGER NOT NULL DEFAULT 0,
                    cancel_requested_at DATETIME,
                    final_result_json TEXT,
                    final_result_sha256 TEXT,
                    finalized_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    UNIQUE(student_id, session_id, idempotency_key_sha256),
                    UNIQUE(run_id, student_id)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_run_plan (
                    run_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    goal_summary TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    PRIMARY KEY(run_id, plan_version),
                    FOREIGN KEY(run_id) REFERENCES agent_run(run_id)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_run_step (
                    run_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    step_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    skill_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    result_sha256 TEXT,
                    error_code TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(run_id, plan_version, step_index),
                    FOREIGN KEY(run_id, plan_version)
                        REFERENCES agent_run_plan(run_id, plan_version)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_run_event (
                    run_id TEXT NOT NULL,
                    student_id TEXT NOT NULL,
                    event_seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    run_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at DATETIME NOT NULL,
                    PRIMARY KEY(run_id, event_seq),
                    FOREIGN KEY(run_id, student_id) REFERENCES agent_run(run_id, student_id)
                )
            """))
            conn.execute(text("""
                CREATE TABLE agent_run_dispatch (
                    run_id TEXT PRIMARY KEY,
                    dispatch_state TEXT NOT NULL,
                    publish_token TEXT,
                    publish_lease_until DATETIME,
                    publish_attempts INTEGER NOT NULL DEFAULT 0,
                    next_publish_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_enqueued_at DATETIME,
                    claim_token TEXT,
                    lease_until DATETIME,
                    claim_attempts INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(run_id) REFERENCES agent_run(run_id) ON DELETE CASCADE
                )
            """))

    def fetch_one(self, statement, params=None):
        with self.engine.connect() as conn:
            row = conn.execute(text(statement), params or {}).mappings().first()
            return dict(row) if row is not None else None

    def fetch_all(self, statement, params=None):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(
                text(statement), params or {},
            ).mappings()]


@pytest.fixture
def store():
    database = SQLiteRunStoreDB()
    yield AgentRunRepository(database)
    database.engine.dispose()


def _submit(store, *, owner="owner_a", session="session_a", key="agent-run-key-01", request=None):
    return store.submit(
        student_id=owner,
        session_id=session,
        idempotency_key=key,
        request_payload=request or {"message": "只读分析当前简历", "interaction_mode": "agent"},
    )


def _plan(store, run_id, *, owner="owner_a", expected=0, steps=None):
    return store.persist_plan(
        run_id,
        student_id=owner,
        expected_plan_version=expected,
        goal_summary="只读分析当前目标并给出下一步建议",
        steps=steps or [{"role": "qa", "goal": "只读解释当前简历内容", "skill_ids": []}],
    )


def test_submit_is_owner_bound_idempotent_and_rejects_payload_conflict(store):
    first = _submit(store)
    assert first["created"] is True
    run = first["run"]
    assert run["status"] == "queued"
    assert run["last_event_seq"] == 1
    assert run["cancel_requested"] is False
    assert run["input_snapshot_version"] == 1
    assert run["input_snapshot_redaction_version"] == 1
    assert "input_snapshot" not in run
    snapshot = store.get_input_snapshot_for_owner(run["run_id"], student_id="owner_a")
    assert snapshot["message"] == "只读分析当前简历"
    assert snapshot["attachment_ids"] == []

    replay = _submit(store)
    assert replay["created"] is False
    assert replay["run"]["run_id"] == run["run_id"]
    assert replay["run"]["last_event_seq"] == 1

    with pytest.raises(AgentRunIdempotencyConflict):
        _submit(store, request={"message": "不同请求", "interaction_mode": "agent"})

    another_owner = _submit(store, owner="owner_b")
    assert another_owner["created"] is True
    assert another_owner["run"]["run_id"] != run["run_id"]
    with pytest.raises(AgentRunNotFound):
        store.get_for_owner(run["run_id"], student_id="owner_b")


def test_resource_read_receipt_is_bound_to_owner_plan_and_started_step(store):
    run_id = _submit(store, key="resource-read-receipt-01")["run"]["run_id"]
    plan = _plan(
        store, run_id,
        steps=[
            {"role": "qa", "goal": "读取绑定资料并回答", "skill_ids": []},
            {"role": "qa", "goal": "基于已核验资料给出总结", "skill_ids": []},
        ],
    )
    store.start_step(
        run_id, student_id="owner_a", plan_version=plan["plan_version"],
        step_index=1,
    )

    assert not store.has_completed_resource_read_for_step(
        run_id, student_id="owner_a", plan_version=1, step_index=1,
    )
    store.record_resource_read(
        run_id, student_id="owner_a", claim_token=None,
        label="本人经历与自述", phase="started",
    )
    store.record_resource_read(
        run_id, student_id="owner_a", claim_token=None,
        label="本人经历与自述", phase="completed",
    )

    assert store.has_completed_resource_read_for_step(
        run_id, student_id="owner_a", plan_version=1, step_index=1,
    )
    events = store.read_events(run_id, student_id="owner_a", limit=10)["events"]
    read_event = next(event for event in events if event["event_type"] == "run.resource_read_completed")
    assert read_event["payload"] == {
        "label": "本人经历与自述", "plan_version": 1, "step_index": 1,
    }
    store.finish_step(
        run_id, student_id="owner_a", plan_version=1, step_index=1,
        outcome="completed", result={"reply_candidate_unverified": "旧步骤结果"},
    )
    revised = _plan(
        store, run_id, expected=1,
        steps=[{"role": "qa", "goal": "新版本读取绑定资料", "skill_ids": []}],
    )
    store.start_step(
        run_id, student_id="owner_a", plan_version=revised["plan_version"],
        step_index=1,
    )
    assert not store.has_completed_resource_read_for_step(
        run_id, student_id="owner_a", plan_version=revised["plan_version"], step_index=1,
    )
    with pytest.raises(AgentRunNotFound):
        store.has_completed_resource_read_for_step(
            run_id, student_id="owner_b", plan_version=1, step_index=1,
        )


def test_submission_and_dispatch_outbox_are_atomic_and_publish_failure_replays(store):
    run_id = _submit(store)["run"]["run_id"]
    dispatch = AgentRunDispatchStore(store)
    assert dispatch.get_for_owner(run_id, student_id="owner_a")["status"] == "pending"

    reserved = dispatch.reserve_publish(run_id)
    assert reserved["reserved"] is True
    assert reserved["state"] == "publishing"
    assert dispatch.finish_publish(
        run_id, publish_token=reserved["publish_token"], published=False,
    ) == "pending"
    state = dispatch.get_for_owner(run_id, student_id="owner_a")
    assert state["status"] == "pending"
    assert state["publish_attempts"] == 1
    assert store.get_for_owner(run_id, student_id="owner_a")["last_event_seq"] == 2
    assert dispatch.due_run_ids() == []  # retry backoff is durable


def test_pre_outbox_run_is_held_until_explicit_idempotent_resubmission(store):
    run_id = _submit(store)["run"]["run_id"]
    dispatch = AgentRunDispatchStore(store)
    with store.mysql.engine.begin() as conn:
        conn.execute(text("""
            UPDATE agent_run_dispatch SET dispatch_state='not_dispatched'
            WHERE run_id=:run_id
        """), {"run_id": run_id})

    # Migration-held rows do not become work merely because Beat runs.
    assert dispatch.due_run_ids() == []
    assert dispatch.reserve_publish(run_id) == {"state": "not_dispatched", "reserved": False}

    # Repeating the owner-bound, idempotent submit may explicitly release it.
    retry = dispatch.reserve_publish(run_id, allow_not_dispatched=True)
    assert retry["reserved"] is True
    assert retry["state"] == "publishing"
    events = store.read_events(run_id, student_id="owner_a", limit=10)["events"]
    assert events[-1]["event_type"] == "run.dispatch_requeued"
    assert events[-1]["payload"] == {"reason": "explicit_idempotent_resubmission"}


def test_published_duplicate_worker_claim_waits_for_executor_without_running(store):
    run_id = _submit(store)["run"]["run_id"]
    dispatch = AgentRunDispatchStore(store)
    reserved = dispatch.reserve_publish(run_id)
    assert dispatch.finish_publish(
        run_id, publish_token=reserved["publish_token"], published=True,
    ) == "published"
    claim = dispatch.claim_worker(run_id)
    assert claim is not None
    assert dispatch.claim_worker(run_id) is None
    assert dispatch.mark_waiting_executor(
        run_id, claim_token=claim["claim_token"],
    ) == "waiting_executor"
    assert dispatch.claim_worker(run_id) is None
    run = store.get_for_owner(run_id, student_id="owner_a")
    assert run["status"] == "queued"
    assert run["last_event_seq"] == 4
    assert [event["event_type"] for event in store.read_events(
        run_id, student_id="owner_a", limit=10,
    )["events"]] == [
        "run.submitted", "run.dispatch_published", "run.dispatch_claimed",
        "run.executor_unavailable",
    ]


def test_dispatch_status_copy_does_not_claim_worker_delivery_is_execution():
    from app.api.agent_runs import _submit_message

    assert "不表示 Agent 已开始执行" in _submit_message("published")
    assert "尚未确认开始" in _submit_message("claimed")
    assert "没有模型或工具执行" in _submit_message("waiting_executor")
    assert "未自动投递" in _submit_message("not_dispatched")


def test_cancel_racing_worker_safe_point_acknowledges_stale_transport_claim(monkeypatch):
    from app.services.agent_runtime import agent_run_jobs

    run_id = "run_" + "a" * 32

    class DispatchStore:
        repository = object()

        def claim_worker(self, supplied_run_id):
            assert supplied_run_id == run_id
            return {"student_id": "owner_a", "claim_token": "b" * 32}

        def get_for_owner(self, supplied_run_id, *, student_id):
            assert supplied_run_id == run_id
            assert student_id == "owner_a"
            return {"status": "cancelled"}

        def claim_is_current(self, supplied_run_id, claim_token):
            assert supplied_run_id == run_id
            assert claim_token == "b" * 32
            return False

        def renew_claim(self, supplied_run_id, *, claim_token):
            assert supplied_run_id == run_id
            assert claim_token == "b" * 32
            return True

    class Executor:
        def __init__(self, repository, dispatch_store):
            assert repository is dispatch_store.repository

        def execute_claim(self, supplied_run_id, *, claim_token, student_id):
            assert supplied_run_id == run_id
            assert claim_token == "b" * 32
            assert student_id == "owner_a"
            raise AgentRunConflict("worker claim is stale or no longer owned")

    monkeypatch.setattr(
        agent_run_jobs, "get_agent_run_dispatch_store", lambda: DispatchStore(),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.agent_run_executor.AgentRunExecutor", Executor,
    )
    result = agent_run_jobs.agent_run_task.run(run_id)
    assert result == {
        "run_id": run_id,
        "dispatch_status": "claim_fenced",
        "current_dispatch_status": "cancelled",
        "execution_started": False,
    }


def test_agent_worker_scope_uses_claim_owner_and_resets_after_exception():
    from app.api._helpers import (
        ScopeError,
        capture_trusted_agent_worker_scope,
        has_trusted_agent_worker_scope,
        resolve_request_scope,
        run_with_trusted_agent_worker_scope,
        trusted_agent_worker_scope,
    )
    from concurrent.futures import ThreadPoolExecutor

    current = {"value": True}
    binding = {
        "student_id": "owner_from_mysql",
        "principal_id": "account_from_mysql",
        "principal_source": "account",
        "principal_auth_epoch": 3,
    }
    run_id, claim_token = "run_" + "c" * 32, "d" * 32

    with pytest.raises(RuntimeError, match="exercise context reset"):
        with trusted_agent_worker_scope(
            run_id=run_id,
            claim_token=claim_token,
            load_claim_binding=lambda rid, token: binding,
            scope_is_current=lambda rid, token: current["value"],
        ):
            scope = resolve_request_scope()
            assert scope.student_id == "owner_from_mysql"
            assert scope.principal_id == "account_from_mysql"
            assert has_trusted_agent_worker_scope() is True
            captured = capture_trusted_agent_worker_scope()
            with ThreadPoolExecutor(max_workers=1) as pool:
                threaded_scope = pool.submit(
                    run_with_trusted_agent_worker_scope,
                    captured,
                    resolve_request_scope,
                ).result(timeout=3)
            assert threaded_scope.student_id == "owner_from_mysql"
            current["value"] = False
            assert has_trusted_agent_worker_scope() is False
            with pytest.raises(ScopeError):
                resolve_request_scope()
            raise RuntimeError("exercise context reset")

    assert has_trusted_agent_worker_scope() is False


def test_failed_step_gets_a_durable_user_reply_before_worker_settlement(store):
    run_id = _submit(store)["run"]["run_id"]
    store.begin_planning(run_id, student_id="owner_a")
    plan = _plan(store, run_id)
    store.start_step(
        run_id,
        student_id="owner_a",
        plan_version=plan["plan_version"],
        step_index=1,
    )
    store.finish_step(
        run_id,
        student_id="owner_a",
        plan_version=plan["plan_version"],
        step_index=1,
        outcome="failed",
        result={"reply_candidate_unverified": "", "verified_facts": []},
        error_code="qa_failed",
    )

    settled = store.fail_run(
        run_id,
        student_id="owner_a",
        reply="只读问答未能完成，请稍后重新提交。",
        error_code="qa_failed",
    )
    assert settled["status"] == "failed"
    assert settled["final_result"] == {
        "reply": "只读问答未能完成，请稍后重新提交。",
        "format": "text",
    }
    assert store.read_events(run_id, student_id="owner_a", limit=20)["events"][-1][
        "event_type"
    ] == "run.failed"


def test_cancel_before_worker_claim_settles_without_dispatch(store):
    run_id = _submit(store)["run"]["run_id"]
    dispatch = AgentRunDispatchStore(store)
    store.request_cancel(run_id, student_id="owner_a")
    cancelled = dispatch.cancel_if_safe(run_id, student_id="owner_a")
    assert cancelled["status"] == "cancelled"
    assert dispatch.get_for_owner(run_id, student_id="owner_a")["status"] == "cancelled"
    assert dispatch.claim_worker(run_id) is None
    assert dispatch.reserve_publish(run_id)["state"] == "cancelled"


def test_expired_transport_claim_requeues_only_before_execution_progress(store):
    run_id = _submit(store)["run"]["run_id"]
    dispatch = AgentRunDispatchStore(store)
    first = dispatch.claim_worker(run_id)
    assert first
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    with store.mysql.engine.begin() as conn:
        conn.execute(text("UPDATE agent_run_dispatch SET lease_until=:past WHERE run_id=:run_id"),
                     {"past": past, "run_id": run_id})

    # A queued run with no plan or started step has no possible business side
    # effect, so the stale transport delivery may be replayed safely.
    replacement = dispatch.reserve_publish(run_id)
    assert replacement["reserved"] is True
    assert replacement["state"] == "publishing"
    assert store.get_for_owner(run_id, student_id="owner_a")["status"] == "queued"


def test_expired_claim_after_running_step_requires_reconciliation_not_rerun(store):
    run_id = _submit(store)["run"]["run_id"]
    dispatch = AgentRunDispatchStore(store)
    claim = dispatch.claim_worker(run_id)
    assert claim
    assert store.begin_planning(run_id, student_id="owner_a")["status"] == "planning"
    plan = _plan(store, run_id)
    store.start_step(
        run_id, student_id="owner_a", plan_version=plan["plan_version"], step_index=1,
    )
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    with store.mysql.engine.begin() as conn:
        conn.execute(text("UPDATE agent_run_dispatch SET lease_until=:past WHERE run_id=:run_id"),
                     {"past": past, "run_id": run_id})

    recovered = dispatch.reserve_publish(run_id)
    assert recovered == {"state": "needs_reconciliation", "reserved": False}
    run = store.get_for_owner(run_id, student_id="owner_a")
    assert run["status"] == "needs_reconciliation"
    step = run["current_plan"]["steps"][0]
    assert step["status"] == "needs_reconciliation"
    assert step["attempts"] == 1
    # A provider may have been dispatched before the worker died. No result or
    # empty/zero-usage ledger is fabricated; the usage outcome is unknown.
    assert step["result"] is None
    assert step["error_code"] == "worker_claim_expired"
    with pytest.raises(AgentRunConflict, match="not ready"):
        store.start_step(
            run_id, student_id="owner_a", plan_version=plan["plan_version"], step_index=1,
        )


def test_request_snapshot_is_bounded_versioned_and_rejects_transport_secrets(store):
    submitted = _submit(store, request={
        "message": "请评估下一步",
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "career-planning",
        "attachment_ids": ["att_123", "att_456"],
    })
    run_id = submitted["run"]["run_id"]
    snapshot = store.get_input_snapshot_for_owner(run_id, student_id="owner_a")
    assert snapshot["message"] == "请评估下一步"
    assert snapshot["expert_id"] == "career-coach"
    assert snapshot["capability_id"] == "career-planning"
    assert snapshot["attachment_ids"] == ["att_123", "att_456"]
    with pytest.raises(ValueError, match="unsupported fields"):
        _submit(
            store,
            key="agent-run-key-02",
            request={"message": "请评估下一步", "authorization": "Bearer do-not-persist"},
        )


def test_opt_in_education_plan_pins_capability_and_store_rejects_skill_omission(store):
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    run_id = _submit(store, key="education-screening-plan-key", request={
        "message": "公告要求硕士及以上；我仅有本科学历。",
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "education-screening",
    })["run"]["run_id"]
    store.begin_planning(run_id, student_id="owner_a")
    capability = resolve_explicit_capability({
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "education-screening",
    })
    metadata = {
        "executor_contract": 1,
        "capability_binding": capability.manifest(),
        "runtime_release": {"release_sha256": "a" * 64},
    }
    steps = [{
        "role": "qa", "goal": "局部对照学历条件并给出下一步",
        "skill_ids": ["education-screening-context", "school-context"],
    }]
    plan = store.persist_plan(
        run_id,
        student_id="owner_a",
        expected_plan_version=0,
        goal_summary="教育条件只读分析",
        steps=steps,
        plan_metadata=metadata,
    )
    assert plan["metadata"]["capability_binding"] == capability.manifest()
    assert plan["steps"][0]["skill_ids"] == steps[0]["skill_ids"]

    with pytest.raises(ValueError, match="selected_capability_role_or_skill_mismatch"):
        store.persist_plan(
            run_id,
            student_id="owner_a",
            expected_plan_version=0,
            goal_summary="教育条件只读分析",
            steps=[{
                "role": "qa", "goal": "遗漏条件技能",
                "skill_ids": ["school-context"],
            }],
            plan_metadata=metadata,
        )


def test_snapshot_validation_rejects_unknown_fields_and_oversized_input(store):
    with pytest.raises(ValueError, match="unsupported fields"):
        _submit(store, request={"message": "x", "cookie": "session-secret"})
    with pytest.raises(ValueError, match="48 KiB"):
        _submit(store, request={"message": "x" * (48 * 1024 + 1)})
    with pytest.raises(ValueError, match="together"):
        _submit(store, request={"message": "x", "expert_id": "career_advisor"})
    with pytest.raises(ValueError, match="allowlist"):
        _submit(store, request={
            "message": "x", "interaction_mode": "experts",
            "expert_id": "career-coach", "capability_id": "unknown-capability",
        })
    with pytest.raises(ValueError, match="interaction_mode=experts"):
        _submit(store, request={
            "message": "x", "interaction_mode": "agent",
            "expert_id": "career-coach", "capability_id": "career-planning",
        })


def test_input_snapshot_can_only_be_read_by_its_owner(store):
    run_id = _submit(store)["run"]["run_id"]
    with pytest.raises(AgentRunNotFound):
        store.get_input_snapshot_for_owner(run_id, student_id="owner_b")
    event_page = store.read_events(run_id, student_id="owner_a")
    assert "input_snapshot" not in event_page["run"]


def test_plan_version_is_cas_idempotent_and_events_use_gap_free_owner_cursor(store):
    run_id = _submit(store)["run"]["run_id"]
    assert store.begin_planning(run_id, student_id="owner_a")["status"] == "planning"
    plan = _plan(store, run_id, expected=0, steps=[
        {"role": "qa", "goal": "先解释已知事实", "skill_ids": []},
        {"role": "planner", "goal": "基于事实给出后续建议", "skill_ids": []},
    ])
    assert plan["plan_version"] == 1
    assert [step["status"] for step in plan["steps"]] == ["pending", "pending"]

    repeated = _plan(store, run_id, expected=0, steps=[
        {"role": "qa", "goal": "先解释已知事实", "skill_ids": []},
        {"role": "planner", "goal": "基于事实给出后续建议", "skill_ids": []},
    ])
    assert repeated["plan_version"] == 1
    assert store.get_for_owner(run_id, student_id="owner_a")["last_event_seq"] == 3

    with pytest.raises(AgentRunConflict, match="different plan|snapshot"):
        _plan(store, run_id, expected=0, steps=[
            {"role": "qa", "goal": "变更后的不同计划", "skill_ids": []},
        ])

    page1 = store.read_events(run_id, student_id="owner_a", after_seq=0, limit=2)
    assert [event["event_seq"] for event in page1["events"]] == [1, 2]
    assert page1["cursor"] == 2 and page1["has_more"] is True
    page2 = store.read_events(run_id, student_id="owner_a", after_seq=page1["cursor"], limit=2)
    assert [event["event_seq"] for event in page2["events"]] == [3]
    assert page2["cursor"] == 3 and page2["has_more"] is False
    with pytest.raises(AgentRunNotFound):
        store.read_events(run_id, student_id="owner_b")
    with pytest.raises(ValueError, match="ahead"):
        store.read_events(run_id, student_id="owner_a", after_seq=4)


def test_cancel_is_a_request_until_worker_reaches_safe_point(store):
    run_id = _submit(store)["run"]["run_id"]
    _plan(store, run_id, steps=[
        {"role": "qa", "goal": "第一步只读确认信息", "skill_ids": []},
        {"role": "planner", "goal": "第二步整理建议", "skill_ids": []},
    ])
    store.start_step(run_id, student_id="owner_a", plan_version=1, step_index=1)

    requested = store.request_cancel(run_id, student_id="owner_a")
    assert requested["status"] == "running"
    assert requested["cancel_requested"] is True
    assert requested["current_plan"]["steps"][0]["status"] == "running"
    with pytest.raises(AgentRunConflict, match="cancel is pending"):
        store.start_step(run_id, student_id="owner_a", plan_version=1, step_index=2)
    with pytest.raises(AgentRunConflict, match="active or uncertain"):
        store.mark_cancelled_at_safe_point(run_id, student_id="owner_a")

    finished = store.finish_step(
        run_id,
        student_id="owner_a",
        plan_version=1,
        step_index=1,
        outcome="completed",
        result={"reply": "确认无学校名称可供核验"},
    )
    assert finished["status"] == "running"
    cancelled = store.mark_cancelled_at_safe_point(run_id, student_id="owner_a")
    assert cancelled["status"] == "cancelled"
    assert cancelled["current_plan"]["steps"][0]["result"] == {"reply": "确认无学校名称可供核验"}
    assert cancelled["current_plan"]["steps"][1]["status"] == "cancelled"


def test_uncertain_write_is_needs_reconciliation_and_cannot_be_restarted(store):
    run_id = _submit(store)["run"]["run_id"]
    _plan(store, run_id, steps=[
        {"role": "planner", "goal": "执行需要外部写入的步骤", "skill_ids": []},
    ])
    store.start_step(run_id, student_id="owner_a", plan_version=1, step_index=1)
    uncertain = store.finish_step(
        run_id,
        student_id="owner_a",
        plan_version=1,
        step_index=1,
        outcome="needs_reconciliation",
        result={"write_outcome": "unknown"},
        error_code="timeout_after_write",
    )
    assert uncertain["status"] == "needs_reconciliation"
    assert uncertain["current_plan"]["steps"][0]["status"] == "needs_reconciliation"
    with pytest.raises(AgentRunConflict, match="ready to start|uncertain"):
        store.start_step(run_id, student_id="owner_a", plan_version=1, step_index=1)
    with pytest.raises(AgentRunConflict, match="no cancel request"):
        store.mark_cancelled_at_safe_point(run_id, student_id="owner_a")
    requested = store.request_cancel(run_id, student_id="owner_a")
    assert requested["status"] == "needs_reconciliation"
    assert requested["cancel_requested"] is True
    with pytest.raises(AgentRunConflict, match="uncertain effects"):
        store.mark_cancelled_at_safe_point(run_id, student_id="owner_a")
    events = store.read_events(run_id, student_id="owner_a", limit=20)["events"]
    assert [event["event_seq"] for event in events] == [1, 2, 3, 4, 5]
    assert events[3]["event_type"] == "run.step_needs_reconciliation"
    assert events[3]["payload"]["result_sha256"]


def test_plan_revision_supersedes_old_pending_steps_but_keeps_completed_result(store):
    run_id = _submit(store)["run"]["run_id"]
    _plan(store, run_id, steps=[
        {"role": "qa", "goal": "只读总结已有资料", "skill_ids": []},
        {"role": "planner", "goal": "根据总结给出建议", "skill_ids": []},
    ])
    store.start_step(run_id, student_id="owner_a", plan_version=1, step_index=1)
    store.finish_step(
        run_id,
        student_id="owner_a",
        plan_version=1,
        step_index=1,
        outcome="completed",
        result={"summary": "只读得到的结论"},
    )

    revised = _plan(
        store,
        run_id,
        expected=1,
        steps=[{"role": "planner", "goal": "接续已确认的总结给出建议", "skill_ids": []}],
    )
    assert revised["plan_version"] == 2
    old_plan = store.get_plan_version_for_owner(
        run_id, 1, student_id="owner_a",
    )
    assert old_plan["steps"][0]["result"] == {"summary": "只读得到的结论"}
    assert old_plan["steps"][1]["status"] == "superseded"


def test_run_is_not_completed_until_final_reply_or_reference_is_persisted(store):
    run_id = _submit(store)["run"]["run_id"]
    _plan(store, run_id)
    store.start_step(run_id, student_id="owner_a", plan_version=1, step_index=1)
    after_step = store.finish_step(
        run_id,
        student_id="owner_a",
        plan_version=1,
        step_index=1,
        outcome="completed",
        result={"summary": "子 Agent 的内部步骤产出"},
    )
    assert after_step["status"] == "steps_completed"
    assert after_step["final_result"] is None
    with pytest.raises(AgentRunConflict, match="finalizing"):
        store.complete_run(run_id, student_id="owner_a", final_result={"reply": "已完成"})

    finalizing = store.begin_finalizing(run_id, student_id="owner_a")
    assert finalizing["status"] == "finalizing"
    completed = store.complete_run(
        run_id,
        student_id="owner_a",
        final_result={"reply": "根据现有信息，建议先补充一段可量化的项目成果。", "format": "text"},
    )
    assert completed["status"] == "completed"
    assert completed["final_result"]["reply"].startswith("根据现有信息")
    assert completed["final_result_sha256"]
    event = store.read_events(run_id, student_id="owner_a", limit=20)["events"][-1]
    assert event["event_type"] == "run.completed"
    assert event["run_status"] == "completed"
    assert event["payload"]["result_sha256"] == completed["final_result_sha256"]
    assert event["payload"]["result_ref"] == f"agent-run://{run_id}/final"
    replay = store.complete_run(
        run_id,
        student_id="owner_a",
        final_result={"reply": "根据现有信息，建议先补充一段可量化的项目成果。", "format": "text"},
    )
    assert replay["status"] == "completed"
    assert replay["last_event_seq"] == completed["last_event_seq"]


def test_final_result_and_completed_event_commit_or_roll_back_together(store):
    run_id = _submit(store)["run"]["run_id"]
    _plan(store, run_id)
    store.start_step(run_id, student_id="owner_a", plan_version=1, step_index=1)
    store.finish_step(
        run_id,
        student_id="owner_a",
        plan_version=1,
        step_index=1,
        outcome="completed",
        result={"summary": "步骤结束"},
    )
    store.begin_finalizing(run_id, student_id="owner_a")
    before = store.get_for_owner(run_id, student_id="owner_a")
    with store.mysql.engine.begin() as conn:
        conn.execute(text("""
            CREATE TRIGGER reject_run_completed_event BEFORE INSERT ON agent_run_event
            WHEN NEW.event_type='run.completed'
            BEGIN SELECT RAISE(ABORT, 'injected final event failure'); END
        """))

    with pytest.raises(Exception, match="injected final event failure"):
        store.complete_run(
            run_id,
            student_id="owner_a",
            final_result={"reply": "主 Agent 的最终答复"},
        )

    after = store.get_for_owner(run_id, student_id="owner_a")
    assert after["status"] == "finalizing"
    assert after["final_result"] is None
    assert after["last_event_seq"] == before["last_event_seq"]


def test_failed_plan_insert_rolls_back_plan_step_status_and_event_sequence(store):
    run_id = _submit(store)["run"]["run_id"]
    with store.mysql.engine.begin() as conn:
        conn.execute(text("""
            CREATE TRIGGER reject_agent_step BEFORE INSERT ON agent_run_step
            BEGIN SELECT RAISE(ABORT, 'injected step insert failure'); END
        """))

    with pytest.raises(Exception, match="injected step insert failure"):
        _plan(store, run_id)

    run = store.get_for_owner(run_id, student_id="owner_a")
    assert run["status"] == "queued"
    assert run["current_plan_version"] == 0
    assert run["last_event_seq"] == 1
    assert run["current_plan"] is None
