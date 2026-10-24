"""Isolated persistence and HTTP coverage for revisioned Agent task control."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "tests"))

from app.api import agent_runs  # noqa: E402
from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore  # noqa: E402
from test_agent_runs_api import run_api  # noqa: E402


def _install_task_schema(repository):
    """Apply only migration 0021's schema to this test's private SQLite DB."""
    with repository.mysql.engine.begin() as conn:
        conn.execute(text("ALTER TABLE agent_run ADD COLUMN current_contract_revision INTEGER NOT NULL DEFAULT 0"))
        conn.execute(text("ALTER TABLE agent_run ADD COLUMN pause_requested_at DATETIME"))
        conn.execute(text("""
            CREATE TABLE agent_run_task_contract_revision (
                run_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                contract_json TEXT NOT NULL,
                contract_sha256 TEXT NOT NULL,
                idempotency_key_sha256 TEXT,
                request_sha256 TEXT NOT NULL,
                mutation_kind TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                PRIMARY KEY(run_id, revision),
                UNIQUE(run_id, idempotency_key_sha256),
                FOREIGN KEY(run_id, student_id) REFERENCES agent_run(run_id, student_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE agent_run_task_write_confirmation (
                run_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                contract_revision INTEGER NOT NULL,
                plan_version INTEGER NOT NULL,
                plan_sha256 TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                target_version TEXT NOT NULL,
                confirmation_id TEXT NOT NULL UNIQUE,
                idempotency_key_sha256 TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                artifact_saved_at DATETIME,
                created_at DATETIME NOT NULL,
                PRIMARY KEY(run_id, contract_revision, plan_version, action),
                UNIQUE(run_id, idempotency_key_sha256),
                FOREIGN KEY(run_id, contract_revision)
                    REFERENCES agent_run_task_contract_revision(run_id, revision),
                FOREIGN KEY(run_id, student_id) REFERENCES agent_run(run_id, student_id),
                FOREIGN KEY(run_id, plan_version)
                    REFERENCES agent_run_plan(run_id, plan_version)
            )
        """))
        conn.execute(text("""
            CREATE TABLE agent_run_task_followup (
                run_id TEXT NOT NULL,
                student_id TEXT NOT NULL,
                idempotency_key_sha256 TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                contract_revision INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                operation_status TEXT NOT NULL,
                model_required INTEGER NOT NULL DEFAULT 1,
                reserved_model_calls INTEGER NOT NULL DEFAULT 0,
                auto_paused INTEGER NOT NULL DEFAULT 0,
                active_slot INTEGER DEFAULT 1,
                classification_json TEXT,
                model_usage_json TEXT,
                result_json TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY(run_id, idempotency_key_sha256),
                UNIQUE(run_id, active_slot),
                FOREIGN KEY(run_id, contract_revision)
                    REFERENCES agent_run_task_contract_revision(run_id, revision),
                FOREIGN KEY(run_id, student_id) REFERENCES agent_run(run_id, student_id)
            )
        """))


def _submit_completed(repository, *, owner="alice", message="制定八周成都后端计划"):
    created = repository.submit(
        student_id=owner,
        session_id="session-alice",
        idempotency_key="completed-run-key-01",
        request_payload={"message": message, "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    plan = repository.persist_plan(
        run_id,
        student_id=owner,
        expected_plan_version=0,
        expected_contract_revision=1,
        goal_summary="制定八周成都后端计划",
        steps=[{"role": "qa", "goal": "读取岗位要求并形成计划", "skill_ids": []}],
        plan_metadata={"executor_contract": 1, "task_contract_revision": 1},
    )
    repository.start_step(
        run_id, student_id=owner, plan_version=plan["plan_version"], step_index=1,
    )
    repository.finish_step(
        run_id, student_id=owner, plan_version=plan["plan_version"], step_index=1,
        outcome="completed", expected_contract_revision=1,
        result={"reply_candidate_unverified": "逐周计划草案", "verified_updates": {}},
    )
    repository.begin_finalizing(run_id, student_id=owner)
    repository.complete_run(
        run_id, student_id=owner,
        expected_contract_revision=1,
        final_result={"reply": "八周成都后端计划已形成，按每周六小时安排。", "format": "text"},
    )
    return run_id


def _observed_usage(prompt_tokens, completion_tokens):
    return {
        "schema_version": 1,
        "status": "observed",
        "reason": "provider_usage_observed",
        "turn_token_cap": 100,
        "call_count": 1,
        "reported_total_tokens": prompt_tokens + completion_tokens,
        "calls": [{
            "index": 1, "stage": "test", "provider": "test", "model": "test",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "finish_reason": "stop", "preflight_reason": "",
        }],
    }


class _MemoryArtifactIndex:
    """Test double for ArtifactCommitter; persistence is asserted by owner readback."""

    def __init__(self):
        self.rows = {}
        self.commit_calls = 0

    def get_for_owner(self, artifact_id, *, student_id):
        row = self.rows.get((artifact_id, student_id))
        return dict(row) if row else None

    def commit_candidates(self, candidates, *, student_id, session_id, turn_id=None):
        self.commit_calls += 1
        for candidate in candidates:
            self.rows[(candidate["artifact_id"], student_id)] = {
                **candidate,
                "preview": dict(candidate["preview"]),
            }
        return []


def test_revision_cas_and_stale_active_read_output_is_fenced(run_api):
    _client, repository = run_api
    _install_task_schema(repository)
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="control-run-key-01",
        request_payload={"message": "制定八周成都后端计划", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    first = repository.get_for_owner(run_id, student_id="alice")
    assert first["task_contract"]["revision"] == 1

    old_plan = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=0,
        expected_contract_revision=1, goal_summary="八周计划",
        steps=[{"role": "qa", "goal": "只读核实岗位", "skill_ids": []}],
        plan_metadata={"executor_contract": 1, "task_contract_revision": 1},
    )
    repository.start_step(run_id, student_id="alice", plan_version=1, step_index=1)
    revised = repository.revise_task_contract(
        run_id, student_id="alice", idempotency_key="revise-http-key-01",
        control={
            "intent": "supplement", "task_id": run_id, "expected_revision": 1,
            "patch": {"add_constraints": ["每周最多六小时"]},
        },
    )
    assert revised["contract"]["revision"] == 2
    assert revised["run"]["status"] == "pause_requested"

    stale = repository.finish_step(
        run_id, student_id="alice", plan_version=1, step_index=1,
        outcome="completed", expected_contract_revision=1,
        result={"reply_candidate_unverified": "旧目标下的答案", "verified_updates": {}},
    )
    assert stale["status"] == "queued"
    assert stale["current_contract_revision"] == 2
    assert stale["current_plan"]["steps"][0]["status"] == "superseded"
    with pytest.raises(Exception):
        repository.persist_plan(
            run_id, student_id="alice", expected_plan_version=1,
            expected_contract_revision=1, goal_summary="旧目标",
            steps=[{"role": "qa", "goal": "继续旧计划", "skill_ids": []}],
            plan_metadata={"executor_contract": 1, "task_contract_revision": 1},
        )


def test_task_usage_sums_hash_verified_planning_and_step_calls(run_api):
    _client, repository = run_api
    _install_task_schema(repository)
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="budget-run-key-01",
        request_payload={"message": "制定八周成都后端计划", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    planner_usage = _observed_usage(12, 4)
    plan = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=0,
        expected_contract_revision=1, goal_summary="八周计划",
        steps=[{"role": "qa", "goal": "读取授权岗位资料", "skill_ids": []}],
        plan_metadata={
            "executor_contract": 1,
            "task_contract_revision": 1,
            "task_model_usage": planner_usage,
        },
    )
    repository.start_step(run_id, student_id="alice", plan_version=1, step_index=1)
    repository.finish_step(
        run_id, student_id="alice", plan_version=1, step_index=1,
        outcome="completed", expected_contract_revision=1,
        result={
            "model_usage": _observed_usage(8, 3),
            "tool_calls": [{"tool_name": "career_data_read", "status": "success"}],
            "verified_updates": {},
        },
    )
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    assert usage == {
        "verified": True, "reason": "usage_observed", "model_calls": 2,
        "tool_calls": 1, "input_tokens": 20, "output_tokens": 7,
        "total_tokens": 27,
    }


def test_task_usage_keeps_superseded_plan_spend_across_replanned_contract(run_api):
    _client, repository = run_api
    _install_task_schema(repository)
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="budget-revision-key-01",
        request_payload={"message": "Revise a bounded plan", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    first = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=0,
        expected_contract_revision=1, goal_summary="Initial plan",
        steps=[{"role": "qa", "goal": "First phase", "skill_ids": []}],
        plan_metadata={
            "executor_contract": 1, "task_contract_revision": 1,
            "task_model_usage": _observed_usage(10, 4),
        },
    )
    assert first["plan_version"] == 1
    revised = repository.revise_task_contract(
        run_id, student_id="alice", idempotency_key="budget-revision-key-02",
        control={
            "intent": "supplement", "task_id": run_id,
            "expected_revision": 1,
            "patch": {"add_constraints": ["Keep the plan concise"]},
        },
    )
    assert revised["contract"]["revision"] == 2
    second = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=1,
        expected_contract_revision=2, goal_summary="Revised plan",
        steps=[{"role": "qa", "goal": "Revised phase", "skill_ids": []}],
        plan_metadata={
            "executor_contract": 1, "task_contract_revision": 2,
            "task_model_usage": _observed_usage(12, 5),
        },
    )
    repository.start_step(
        run_id, student_id="alice", plan_version=second["plan_version"], step_index=1,
    )
    repository.finish_step(
        run_id, student_id="alice", plan_version=second["plan_version"],
        step_index=1, outcome="completed", expected_contract_revision=2,
        result={
            "model_usage": _observed_usage(8, 3),
            "tool_calls": [{"tool_name": "career_data_read", "status": "success"}],
            "verified_updates": {},
        },
    )

    # A new plan version and a fresh worker phase still account for the
    # superseded planner and the current step as one task-wide budget.
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    assert usage == {
        "verified": True, "reason": "usage_observed", "model_calls": 3,
        "tool_calls": 1, "input_tokens": 30, "output_tokens": 12,
        "total_tokens": 42,
    }


def test_pause_during_finalizing_keeps_a_draft_checkpoint_for_resume(run_api):
    _client, repository = run_api
    _install_task_schema(repository)
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="pause-finalize-key-01",
        request_payload={"message": "制定八周成都后端计划", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    plan = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=0,
        expected_contract_revision=1, goal_summary="八周计划",
        steps=[{"role": "qa", "goal": "形成可核验计划", "skill_ids": []}],
        plan_metadata={
            "executor_contract": 1,
            "task_contract_revision": 1,
            "task_model_usage": _observed_usage(2, 1),
        },
    )
    repository.start_step(run_id, student_id="alice", plan_version=1, step_index=1)
    repository.finish_step(
        run_id, student_id="alice", plan_version=1, step_index=1,
        outcome="completed", expected_contract_revision=1,
        result={"model_usage": _observed_usage(3, 2), "verified_updates": {}},
    )
    repository.begin_finalizing(run_id, student_id="alice")
    requested = repository.request_pause(run_id, student_id="alice")
    assert requested["status"] == "pause_requested"

    checkpoint = repository.complete_run(
        run_id, student_id="alice", expected_contract_revision=1,
        final_result={
            "reply": "八周计划草稿",
            "format": "text",
            "finalizer_model_usage": _observed_usage(7, 4),
        },
    )
    assert checkpoint["status"] == "paused"
    assert checkpoint["final_result_checkpoint"] is True
    assert checkpoint["finalized_at"] is None
    checkpoint_usage = repository.task_usage_for_owner(run_id, student_id="alice")
    assert checkpoint_usage == {
        "verified": True, "reason": "usage_observed", "model_calls": 3,
        "tool_calls": 0, "input_tokens": 12, "output_tokens": 7,
        "total_tokens": 19,
    }

    resumed = repository.resume_task(
        run_id, student_id="alice", expected_revision=1,
    )
    assert resumed["status"] == "finalizing"
    assert resumed["final_result_checkpoint"] is True
    completed = repository.complete_run(
        run_id, student_id="alice", expected_contract_revision=1,
        final_result=checkpoint["final_result"],
    )
    assert completed["status"] == "completed"
    assert completed["final_result"]["reply"] == "八周计划草稿"
    assert repository.task_usage_for_owner(run_id, student_id="alice") == checkpoint_usage


def test_budget_stop_can_settle_a_failed_step_as_partial_without_replay(run_api):
    _client, repository = run_api
    _install_task_schema(repository)
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="partial-run-key-01",
        request_payload={"message": "制定八周成都后端计划", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    plan = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=0,
        expected_contract_revision=1, goal_summary="八周计划",
        steps=[{"role": "qa", "goal": "读取授权岗位资料", "skill_ids": []}],
        plan_metadata={
            "executor_contract": 1, "task_contract_revision": 1,
            "task_model_usage": _observed_usage(2, 1),
        },
    )
    repository.start_step(run_id, student_id="alice", plan_version=1, step_index=1)
    repository.finish_step(
        run_id, student_id="alice", plan_version=1, step_index=1,
        outcome="failed", expected_contract_revision=1,
        error_code="turn_token_cap_exceeded",
        result={
            "model_usage": {
                **_observed_usage(4, 1),
                "status": "unverified",
                "reason": "turn_token_cap_exceeded",
            },
            "verified_updates": {},
        },
    )
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    assert usage == {
        "verified": False, "reason": "turn_token_cap_exceeded", "model_calls": 2,
        "tool_calls": 0, "input_tokens": 6, "output_tokens": 2,
        "total_tokens": 8,
    }
    partial = repository.mark_partial(
        run_id, student_id="alice",
        final_result={"reply": "预算已达到；当前只完成了前序可核验工作。", "format": "text"},
        reason="task_budget_exhausted",
    )
    assert partial["status"] == "partial"
    assert partial["final_result"]["reply"].startswith("预算已达到")
    dispatch = AgentRunDispatchStore(repository)
    assert dispatch.get_for_owner(run_id, student_id="alice")["status"] == "terminal"
    assert run_id not in dispatch.due_run_ids()
    assert dispatch.claim_worker(run_id) is None


def test_failed_read_tool_result_is_terminal_and_cannot_be_replayed(run_api):
    _client, repository = run_api
    _install_task_schema(repository)
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="tool-failure-run-001",
        request_payload={"message": "Read the authorized role evidence", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    plan = repository.persist_plan(
        run_id, student_id="alice", expected_plan_version=0,
        expected_contract_revision=1, goal_summary="Read role evidence",
        steps=[{"role": "qa", "goal": "Read authorized evidence", "skill_ids": []}],
        plan_metadata={"executor_contract": 1, "task_contract_revision": 1,
                       "task_model_usage": _observed_usage(4, 2)},
    )
    repository.start_step(
        run_id, student_id="alice", plan_version=plan["plan_version"], step_index=1,
    )
    repository.finish_step(
        run_id, student_id="alice", plan_version=plan["plan_version"], step_index=1,
        outcome="failed", error_code="tool_execution_error",
        expected_contract_revision=1,
        result={
            "model_usage": _observed_usage(3, 1), "tool_call_count": 1,
            "tool_calls": [{"tool_name": "career_data_read", "status": "error",
                            "error_code": "tool_execution_error", "retryable": False}],
            "verified_updates": {},
        },
    )
    failed = repository.fail_run(
        run_id, student_id="alice", reply="授权岗位资料读取失败，任务未完成。",
        error_code="tool_execution_error",
    )

    dispatch = AgentRunDispatchStore(repository)
    assert failed["status"] == "failed"
    assert repository.get_for_owner(run_id, student_id="alice")["current_plan"]["steps"][0]["status"] == "failed"
    assert dispatch.get_for_owner(run_id, student_id="alice")["status"] == "terminal"
    assert run_id not in dispatch.due_run_ids()
    assert dispatch.claim_worker(run_id) is None


def test_expired_planner_claim_is_reconciled_instead_of_reissuing_unknown_model_call(run_api):
    _client, repository = run_api
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="planner-claim-key-01",
        request_payload={"message": "制定八周成都后端计划", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    dispatch = AgentRunDispatchStore(repository)
    reservation = dispatch.reserve_publish(run_id)
    dispatch.finish_publish(
        run_id, publish_token=reservation["publish_token"], published=True,
    )
    claim = dispatch.claim_worker(run_id)
    assert claim is not None
    with repository.mysql.engine.begin() as conn:
        conn.execute(text("UPDATE agent_run SET status='planning' WHERE run_id=:run_id"), {
            "run_id": run_id,
        })
        conn.execute(text("""
            UPDATE agent_run_dispatch SET lease_until=:expired WHERE run_id=:run_id
        """), {"run_id": run_id, "expired": "2000-01-01 00:00:00"})

    assert dispatch.claim_worker(run_id) is None
    assert repository.get_for_owner(run_id, student_id="alice")["status"] == "needs_reconciliation"
    assert dispatch.get_for_owner(run_id, student_id="alice")["status"] == "needs_reconciliation"


def test_simple_direct_answer_completes_without_creating_a_fake_plan(run_api):
    _client, repository = run_api
    _install_task_schema(repository)
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="direct-answer-key-01",
        request_payload={"message": "简单解释一下幂等键", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    repository.begin_planning(run_id, student_id="alice")
    answer = repository.complete_direct_run(
        run_id, student_id="alice", expected_contract_revision=1,
        final_result={
            "reply": "幂等键用于识别同一请求的重试。",
            "format": "text",
            "planner_model_usage": _observed_usage(6, 3),
        },
    )
    assert answer["status"] == "completed"
    assert answer["current_plan_version"] == 0
    assert answer["current_plan"] is None
    assert answer["final_result"]["reply"].startswith("幂等键")
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    assert usage["verified"] is True
    assert usage["model_calls"] == 1
    assert usage["total_tokens"] == 9

    interrupted = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="direct-pause-key-01",
        request_payload={"message": "简单解释一下恢复", "interaction_mode": "agent"},
    )
    paused_run_id = interrupted["run"]["run_id"]
    repository.begin_planning(paused_run_id, student_id="alice")
    assert repository.request_pause(paused_run_id, student_id="alice")["status"] == "pause_requested"
    checkpoint = repository.complete_direct_run(
        paused_run_id, student_id="alice", expected_contract_revision=1,
        final_result={"reply": "恢复会从持久检查点继续。", "format": "text"},
    )
    assert checkpoint["status"] == "paused"
    assert checkpoint["final_result_checkpoint"] is True
    assert repository.resume_task(
        paused_run_id, student_id="alice", expected_revision=1,
    )["status"] == "finalizing"
    assert repository.complete_direct_run(
        paused_run_id, student_id="alice", expected_contract_revision=1,
        final_result=checkpoint["final_result"],
    )["status"] == "completed"


def test_lead_can_choose_direct_response_without_persisting_a_plan(run_api, monkeypatch):
    _client, repository = run_api
    _install_task_schema(repository)
    created = repository.submit(
        student_id="alice", session_id="session-alice",
        idempotency_key="direct-executor-key-01",
        request_payload={"message": "简单解释幂等", "interaction_mode": "agent"},
    )
    run_id = created["run"]["run_id"]
    contract = repository.get_task_contract_for_owner(run_id, student_id="alice")
    dispatch = AgentRunDispatchStore(repository)
    reservation = dispatch.reserve_publish(run_id)
    dispatch.finish_publish(
        run_id, publish_token=reservation["publish_token"], published=True,
    )
    claim = dispatch.claim_worker(run_id)
    assert claim is not None

    from app.services.agent_runtime import agent_run_executor, experts_lead
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    from app.services.agent_runtime.budget import ContextWindowStatus, record_model_call

    def decide(*_args, **_kwargs):
        record_model_call(
            provider="fixture", model="fixture-model", stage="lead",
            usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            preflight=ContextWindowStatus(
                provider="fixture", model="fixture-model", context_window_tokens=64,
                input_limit_tokens=60, estimated_input_tokens=4,
                remaining_input_tokens=56, requested_output_tokens=8,
                fits=True, estimate_method="test", reason="fits",
            ),
            finish_reason="stop",
        )
        return {"action": "respond", "reply": "幂等键用于识别重复请求。"}

    monkeypatch.setattr(experts_lead, "experts_lead_decide", decide)
    monkeypatch.setattr(agent_run_executor, "_base_runtime_release", lambda: {"test": "v1"})
    executor = AgentRunExecutor(repository, dispatch)
    done = executor._plan(
        run_id, "alice", claim["claim_token"],
        {"message": "简单解释幂等", "interaction_mode": "agent"},
        {"current_plan_version": 0, "session_id": "session-alice"},
        contract=contract,
    )
    run = repository.get_for_owner(run_id, student_id="alice")
    assert done is True
    assert run["status"] == "completed"
    assert run["current_plan_version"] == 0
    assert "幂等键用于识别" in run["final_result"]["reply"]
    assert repository.task_usage_for_owner(run_id, student_id="alice")["model_calls"] == 1


def test_save_exact_plan_version_is_owner_read_back_and_idempotent(run_api, monkeypatch):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_completed(repository)
    index = _MemoryArtifactIndex()
    monkeypatch.setattr(
        "app.services.agent_runtime.artifact_committer.get_artifact_committer",
        lambda: index,
    )

    url = f"/api/agent/runs/{run_id}/save"
    headers = {"X-Test-Owner": "alice", "Idempotency-Key": "save-plan-key-01"}
    response = client.post(url, headers=headers,
                           json={"contract_revision": 1, "plan_version": 1})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()["data"]
    assert body["confirmation"]["saved"] is True
    assert body["artifact"]["preview"] == {
        "type": "plan_preview", "steps": 1, "completed": 1,
    }
    assert body["artifact"]["resource_id"] == f"{run_id}:plan:1"
    assert index.commit_calls == 1
    assert len(body["run"]["run"]["saved_artifacts"]) == 1
    plan_read = client.get(
        f"/api/agent/runs/{run_id}/plans/1",
        headers={"X-Test-Owner": "alice"},
    )
    assert plan_read.status_code == 200
    assert plan_read.get_json()["data"]["plan_sha256"] == body["confirmation"]["plan_sha256"]

    replay = client.post(url, headers=headers,
                         json={"contract_revision": 1, "plan_version": 1})
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()["data"]["confirmation"]["artifact_id"] == body["confirmation"]["artifact_id"]
    assert index.commit_calls == 1

    forbidden_owner = client.post(
        url,
        headers={"X-Test-Owner": "mallory", "Idempotency-Key": "save-plan-key-02"},
        json={"contract_revision": 1, "plan_version": 1},
    )
    assert forbidden_owner.status_code == 404


def test_save_retry_reads_existing_artifact_after_confirmation_write_outcome_is_unknown(
    run_api, monkeypatch,
):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_completed(repository)
    index = _MemoryArtifactIndex()
    monkeypatch.setattr(
        "app.services.agent_runtime.artifact_committer.get_artifact_committer",
        lambda: index,
    )
    original_mark_verified = repository.mark_artifact_save_verified
    verify_attempts = []

    def fail_after_artifact_readback(*args, **kwargs):
        verify_attempts.append(1)
        if len(verify_attempts) == 1:
            raise RuntimeError("simulated loss after external artifact commit and readback")
        return original_mark_verified(*args, **kwargs)

    monkeypatch.setattr(repository, "mark_artifact_save_verified", fail_after_artifact_readback)
    url = f"/api/agent/runs/{run_id}/save"
    headers = {"X-Test-Owner": "alice", "Idempotency-Key": "save-unknown-outcome-001"}
    body = {"contract_revision": 1, "plan_version": 1}

    uncertain = client.post(url, headers=headers, json=body)
    assert uncertain.status_code == 503, uncertain.get_json()
    assert index.commit_calls == 1
    assert repository.get_for_owner(run_id, student_id="alice")["saved_artifacts"] == []

    recovered = client.post(url, headers=headers, json=body)
    assert recovered.status_code == 200, recovered.get_json()
    assert recovered.get_json()["data"]["confirmation"]["saved"] is True
    assert index.commit_calls == 1
    assert len(repository.get_for_owner(run_id, student_id="alice")["saved_artifacts"]) == 1


def test_control_route_revises_once_and_rejects_stale_revision(run_api):
    client, repository = run_api
    _install_task_schema(repository)
    response = client.post(
        "/api/agent/runs",
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "control-http-key-01"},
        json={"session_id": "session-alice", "message": "制定八周成都后端计划"},
    )
    assert response.status_code == 201
    run_id = response.get_json()["data"]["run"]["run_id"]
    control_url = f"/api/agent/runs/{run_id}/control"
    headers = {"X-Test-Owner": "alice", "Idempotency-Key": "revision-http-key-01"}
    control = {
        "intent": "supplement", "task_id": run_id, "expected_revision": 1,
        "patch": {"add_constraints": ["每周最多六小时"]},
    }
    first = client.post(control_url, headers=headers, json=control)
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["data"]["run"]["task_contract"]["revision"] == 2
    replay = client.post(control_url, headers=headers, json=control)
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()["created_revision"] is False

    stale = client.post(
        control_url,
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "revision-http-key-02"},
        json={**control, "expected_revision": 1, "patch": {"add_constraints": ["周日休息"]}},
    )
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "AGENT_RUN_STALE_REVISION"
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2


def test_pause_blocks_replay_until_revision_bound_resume(run_api):
    client, repository = run_api
    _install_task_schema(repository)
    response = client.post(
        "/api/agent/runs",
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "pause-http-key-01"},
        json={"session_id": "session-alice", "message": "制定八周成都后端计划"},
    )
    run_id = response.get_json()["data"]["run"]["run_id"]
    control = {
        "intent": "pause", "task_id": run_id, "expected_revision": 1,
    }
    paused = client.post(
        f"/api/agent/runs/{run_id}/control",
        headers={"X-Test-Owner": "alice"}, json=control,
    )
    assert paused.status_code == 200, paused.get_json()
    assert paused.get_json()["data"]["run"]["status"] == "paused"
    assert AgentRunDispatchStore(repository).reserve_publish(run_id) == {
        "state": "paused", "reserved": False,
    }

    resumed = client.post(
        f"/api/agent/runs/{run_id}/resume",
        headers={"X-Test-Owner": "alice"}, json={"expected_revision": 1},
    )
    assert resumed.status_code == 200, resumed.get_json()
    assert resumed.get_json()["data"]["run"]["status"] == "queued"
    assert AgentRunDispatchStore(repository).get_for_owner(
        run_id, student_id="alice",
    )["status"] == "pending"


def _submit_open_task(repository, *, owner="alice"):
    created = repository.submit(
        student_id=owner,
        session_id=f"session-{owner}",
        idempotency_key=f"open-task-key-{owner}",
        request_payload={"message": "Create a backend study plan", "interaction_mode": "agent"},
    )
    return created["run"]["run_id"]


def _followup_usage_ledger(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def fake_capture(*_args, **kwargs):
        ledger = {"calls": [], "turn_token_cap": kwargs.get("turn_token_cap"),
                  "stop_reason": None, "budget_stop": None}
        yield ledger
        ledger["calls"].append({"usage": {
            "prompt_tokens": 35, "completion_tokens": 8, "total_tokens": 43,
        }})

    monkeypatch.setattr(
        "app.services.agent_runtime.budget.capture_usage_ledger", fake_capture,
    )


def _bind_followup_context(monkeypatch, run_id):
    monkeypatch.setattr(
        "app.services.agent_runtime.task_context.TaskContextStore.read",
        lambda self, session_id, owner, incarnation=None: {
            "active_run_id": run_id, "last_run_id": None,
        },
    )


def test_natural_followup_revises_contract_once_and_persists_classifier_usage(
    run_api, monkeypatch,
):
    from app.services.agent_runtime.agent_task_followup import (
        FollowupIntent, TaskFollowupClassification,
    )

    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    _followup_usage_ledger(monkeypatch)
    classifier_calls = []

    def classify(message, *, contract, run):
        classifier_calls.append((message, contract.revision, run["run_id"]))
        return TaskFollowupClassification(
            intent=FollowupIntent.SUPPLEMENT, confidence=0.98,
            patch={"add_constraints": ["Six hours per week"]},
            clarification_question="", requested_plan_version=None,
            reason="The user added a weekly time limit.",
        )

    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup", classify,
    )
    body = {"task_id": run_id, "expected_revision": 1,
            "message": "Change the weekly time to six hours"}
    headers = {"X-Test-Owner": "alice", "Idempotency-Key": "followup-revision-001"}
    first = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["data"]["result"]["action"] == "contract_revised"
    current = repository.get_for_owner(run_id, student_id="alice")
    assert current["task_contract"]["revision"] == 2
    assert current["task_contract"]["constraints"] == ["Six hours per week"]
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    assert usage["verified"] is True
    assert usage["model_calls"] == 1

    replay = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()["data"]["result"]["action"] == "contract_revised"
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2
    assert len(classifier_calls) == 1
    conflict = client.post(
        f"/api/agent/runs/{run_id}/followup",
        json={**body, "message": "Change the weekly time to ten hours"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert len(classifier_calls) == 1


@pytest.mark.parametrize(("message", "expected_intent"), [
    (
        "请补充当前计划：加入一道并发控制自测题，并写明答案要点。",
        "supplement",
    ),
    (
        "请将当前任务目标修改为：Create a four-week backend study plan",
        "revise_goal",
    ),
])
def test_api_persists_explicit_deterministic_followup_without_model_reservation(
    run_api, monkeypatch, message, expected_intent,
):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    dispatched = []

    def no_model_call(*_args, **_kwargs):
        pytest.fail("explicit deterministic follow-up must not invoke a classifier")

    def no_reservation(*_args, **_kwargs):
        pytest.fail("deterministic follow-up must not reserve a model call")

    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup",
        no_model_call,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.budget.capture_usage_ledger", no_model_call,
    )
    monkeypatch.setattr(
        repository, "reserve_task_followup_model_call", no_reservation,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.agent_run_jobs.dispatch_agent_run",
        lambda *args, **kwargs: dispatched.append((args, kwargs)) or "pending",
    )

    body = {"task_id": run_id, "expected_revision": 1, "message": message}
    headers = {"X-Test-Owner": "alice", "Idempotency-Key": "deterministic-followup-001"}
    first = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)

    assert first.status_code == 200, first.get_json()
    data = first.get_json()["data"]
    assert data["operation_status"] == "completed"
    assert data["classification"]["intent"] == expected_intent
    assert data["result"]["action"] == "contract_revised"
    current = repository.get_for_owner(run_id, student_id="alice")
    assert current["task_contract"]["revision"] == 2
    if expected_intent == "supplement":
        assert current["task_contract"]["acceptance_criteria"][-1] == message
        assert current["task_contract"]["goal"] == "Create a backend study plan"
    else:
        assert current["task_contract"]["goal"] == "Create a four-week backend study plan"
    operation = repository.get_task_followup_operation(
        run_id, student_id="alice", idempotency_key=headers["Idempotency-Key"],
    )["operation"]
    assert operation["reserved_model_calls"] == 0
    assert len(dispatched) == 1

    replay = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()["data"]["result"]["action"] == "contract_revised"
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2
    assert len(dispatched) == 1

    conflict = client.post(
        f"/api/agent/runs/{run_id}/followup",
        json={**body, "message": message + " 再新建任务"}, headers=headers,
    )
    assert conflict.status_code == 409
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2


def test_followup_reconciles_revision_committed_before_dispatch_result_is_lost(
    run_api, monkeypatch,
):
    from app.services.agent_runtime.agent_task_followup import (
        FollowupIntent, TaskFollowupClassification,
    )

    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    _followup_usage_ledger(monkeypatch)
    classifier_calls = []
    dispatch_calls = []

    def classify(message, *, contract, run):
        classifier_calls.append((message, contract.revision, run["run_id"]))
        return TaskFollowupClassification(
            intent=FollowupIntent.SUPPLEMENT, confidence=0.98,
            patch={"add_constraints": ["Six hours per week"]},
            clarification_question="", requested_plan_version=None,
            reason="The user added a weekly time limit.",
        )

    def lose_first_dispatch_result(*_args, **_kwargs):
        dispatch_calls.append(1)
        if len(dispatch_calls) == 1:
            raise RuntimeError("simulated response loss after revision commit")
        return "pending"

    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup", classify,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.agent_run_jobs.dispatch_agent_run",
        lose_first_dispatch_result,
    )
    body = {"task_id": run_id, "expected_revision": 1,
            "message": "Change the weekly time to six hours"}
    headers = {"X-Test-Owner": "alice", "Idempotency-Key": "followup-recover-001"}
    first = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert first.status_code == 503, first.get_json()
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2

    recovered = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert recovered.status_code == 200, recovered.get_json()
    assert recovered.get_json()["data"]["result"]["action"] == "contract_revised"
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2
    assert len(classifier_calls) == 1
    assert len(dispatch_calls) == 2


def test_server_recovers_persisted_classification_without_client_key_or_second_model_call(
    run_api, monkeypatch,
):
    from app.services.agent_runtime.agent_task_followup import (
        FollowupIntent, TaskFollowupClassification,
    )
    import app.api.agent_runs as agent_runs_api

    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    _followup_usage_ledger(monkeypatch)
    classifier_calls = []

    def classify(message, *, contract, run):
        classifier_calls.append((message, contract.revision, run["run_id"]))
        return TaskFollowupClassification(
            intent=FollowupIntent.SUPPLEMENT, confidence=0.98,
            patch={"add_constraints": ["One isolation-level self-test"]},
            clarification_question="", requested_plan_version=None,
            reason="The user added one test to the existing plan.",
        )

    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup", classify,
    )
    real_apply = agent_runs_api._apply_task_followup
    apply_attempts = []

    def lose_response_after_classification(*_args, **_kwargs):
        apply_attempts.append(1)
        raise RuntimeError("simulated process interruption after durable classification")

    monkeypatch.setattr(agent_runs_api, "_apply_task_followup", lose_response_after_classification)
    body = {
        "task_id": run_id, "expected_revision": 1,
        "message": "Add one isolation-level self-test to the existing plan; do not save yet.",
    }
    first = client.post(
        f"/api/agent/runs/{run_id}/followup", json=body,
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "followup-recovery-001"},
    )
    assert first.status_code == 503
    assert len(classifier_calls) == 1
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 1

    monkeypatch.setattr(agent_runs_api, "_apply_task_followup", real_apply)
    recovered = client.post(
        f"/api/agent/runs/{run_id}/followup/recover",
        headers={"X-Test-Owner": "alice"}, json={},
    )
    assert recovered.status_code == 200, recovered.get_json()
    data = recovered.get_json()["data"]
    assert data["operation_status"] == "completed"
    assert data["request"] == body
    assert data["result"]["action"] == "contract_revised"
    assert "_operation_key_sha256" not in data
    after = repository.get_for_owner(run_id, student_id="alice")
    assert after["task_contract"]["revision"] == 2
    assert after["task_contract"]["constraints"] == ["One isolation-level self-test"]
    assert len(classifier_calls) == 1

    repeated = client.post(
        f"/api/agent/runs/{run_id}/followup/recover",
        headers={"X-Test-Owner": "alice"}, json={},
    )
    assert repeated.status_code == 200, repeated.get_json()
    assert repeated.get_json()["data"]["operation_status"] == "none"
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2
    assert len(classifier_calls) == 1

    wrong_owner = client.post(
        f"/api/agent/runs/{run_id}/followup/recover",
        headers={"X-Test-Owner": "mallory"}, json={},
    )
    assert wrong_owner.status_code == 404


def test_recover_owner_bound_deterministic_followup_with_no_recorded_model_call(
    run_api, monkeypatch,
):
    import app.api.agent_runs as agent_runs_api

    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    message = "请补充当前计划：加入一道并发控制自测题，并写明答案要点。"
    idempotency_key = "deterministic-recovery-001"
    repository.begin_task_followup_operation(
        run_id, student_id="alice", expected_revision=1, message=message,
        idempotency_key=idempotency_key, model_required=True,
    )
    repository.mark_task_followup_uncertain(
        run_id, student_id="alice", idempotency_key=idempotency_key,
        model_usage={
            "status": "unverified", "reason": "provider_usage_unverified",
            "call_count": 0, "calls": [],
        },
    )

    def no_model_call(*_args, **_kwargs):
        pytest.fail("deterministic recovery must not invoke the model classifier")

    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup",
        no_model_call,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.budget.capture_usage_ledger", no_model_call,
    )
    dispatches = []
    monkeypatch.setattr(
        "app.services.agent_runtime.agent_run_jobs.dispatch_agent_run",
        lambda *args, **kwargs: dispatches.append((args, kwargs)) or "pending",
    )

    recovered = client.post(
        f"/api/agent/runs/{run_id}/followup/recover",
        headers={"X-Test-Owner": "alice"}, json={},
    )
    assert recovered.status_code == 200, recovered.get_json()
    data = recovered.get_json()["data"]
    assert data["operation_status"] == "completed"
    assert data["classification"]["intent"] == "supplement"
    assert data["result"]["action"] == "contract_revised"
    assert "_operation_key_sha256" not in data

    operation = repository.get_task_followup_operation(
        run_id, student_id="alice", idempotency_key=idempotency_key,
    )["operation"]
    assert operation["status"] == "completed"
    assert operation["model_required"] is False
    assert operation["reserved_model_calls"] == 0
    assert operation["model_usage"]["status"] == "unverified"
    assert operation["model_usage"]["call_count"] == 0
    current = repository.get_for_owner(run_id, student_id="alice")
    assert current["task_contract"]["revision"] == 2
    assert current["task_contract"]["acceptance_criteria"][-1] == message
    assert len(dispatches) == 1

    event_types = [
        event["event_type"] for event in repository.read_events(
            run_id, student_id="alice", after_seq=0, limit=200,
        )["events"]
    ]
    assert "run.followup_needs_reconciliation" in event_types
    assert "run.followup_deterministic_reconciled" in event_types
    assert "run.followup_applied" in event_types

    repeated = client.post(
        f"/api/agent/runs/{run_id}/followup/recover",
        headers={"X-Test-Owner": "alice"}, json={},
    )
    assert repeated.status_code == 200, repeated.get_json()
    assert repeated.get_json()["data"]["operation_status"] == "none"
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2
    assert len(dispatches) == 1

    wrong_owner = client.post(
        f"/api/agent/runs/{run_id}/followup/recover",
        headers={"X-Test-Owner": "mallory"}, json={},
    )
    assert wrong_owner.status_code == 404


@pytest.mark.parametrize(("message", "call_count"), [
    ("What should I do next?", 0),
    ("请补充当前计划：加入一道并发控制自测题。", 1),
])
def test_deterministic_recovery_rejects_ambiguous_or_recorded_provider_call(
    run_api, monkeypatch, message, call_count,
):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    idempotency_key = "deterministic-recovery-reject-001"
    repository.begin_task_followup_operation(
        run_id, student_id="alice", expected_revision=1, message=message,
        idempotency_key=idempotency_key, model_required=True,
    )
    recorded_calls = ([{"usage": {"prompt_tokens": 2, "completion_tokens": 1,
                                    "total_tokens": 3}}]
                      if call_count else [])
    repository.mark_task_followup_uncertain(
        run_id, student_id="alice", idempotency_key=idempotency_key,
        model_usage={
            "status": "unverified", "reason": "provider_usage_unverified",
            "call_count": call_count, "calls": recorded_calls,
        },
    )
    response = client.post(
        f"/api/agent/runs/{run_id}/followup/recover",
        headers={"X-Test-Owner": "alice"}, json={},
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["operation_status"] == "none"
    operation = repository.get_task_followup_operation(
        run_id, student_id="alice", idempotency_key=idempotency_key,
    )["operation"]
    assert operation["status"] == "needs_reconciliation"
    assert operation["classification"] is None
    assert operation["result"] is None
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 1


def test_deterministic_recovery_does_not_apply_a_stale_contract_revision(
    run_api, monkeypatch,
):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    message = "请补充当前计划：加入一道并发控制自测题，并写明答案要点。"
    idempotency_key = "deterministic-recovery-stale-001"
    repository.begin_task_followup_operation(
        run_id, student_id="alice", expected_revision=1, message=message,
        idempotency_key=idempotency_key, model_required=True,
    )
    repository.mark_task_followup_uncertain(
        run_id, student_id="alice", idempotency_key=idempotency_key,
        model_usage={
            "status": "unverified", "reason": "provider_usage_unverified",
            "call_count": 0, "calls": [],
        },
    )
    repository.revise_task_contract(
        run_id, student_id="alice", idempotency_key="recovery-stale-control-001",
        control={
            "intent": "supplement", "task_id": run_id, "expected_revision": 1,
            "patch": {"add_constraints": ["Keep the existing task constraints"]},
        },
    )

    response = client.post(
        f"/api/agent/runs/{run_id}/followup/recover",
        headers={"X-Test-Owner": "alice"}, json={},
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["operation_status"] == "none"
    operation = repository.get_task_followup_operation(
        run_id, student_id="alice", idempotency_key=idempotency_key,
    )["operation"]
    assert operation["status"] == "needs_reconciliation"
    assert operation["classification"] is None
    assert operation["result"] is None
    assert repository.get_for_owner(run_id, student_id="alice")["task_contract"]["revision"] == 2


def test_completed_current_run_can_be_revised_without_overwriting_saved_history(
    run_api, monkeypatch,
):
    from app.services.agent_runtime.agent_task_followup import (
        FollowupIntent, TaskFollowupClassification,
    )

    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_completed(repository)
    _bind_followup_context(monkeypatch, run_id)
    _followup_usage_ledger(monkeypatch)
    before = repository.get_for_owner(run_id, student_id="alice")
    assert before["status"] == "completed"
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    classifier_calls = []

    def observed_usage(*_args, **_kwargs):
        return {
            **usage, "verified": True, "reason": "test_fixture_observed",
            "model_calls": len(classifier_calls), "tool_calls": 0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        }

    monkeypatch.setattr(
        repository, "task_usage_for_owner", observed_usage,
    )
    dispatch_calls = []

    def classify(*_args, **_kwargs):
        classifier_calls.append(1)
        return TaskFollowupClassification(
            intent=FollowupIntent.SUPPLEMENT, confidence=0.98,
            patch={"add_constraints": ["Add one isolation-level self-test"]},
            clarification_question="", requested_plan_version=None,
            reason="The user adds one verifiable item to the current plan.",
        )

    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup", classify,
    )

    def dispatch(run_id, **_kwargs):
        dispatch_calls.append(run_id)
        return "pending"

    monkeypatch.setattr(
        "app.services.agent_runtime.agent_run_jobs.dispatch_agent_run", dispatch,
    )
    body = {
        "task_id": run_id, "expected_revision": 1,
        "message": "Add one self-test to the current plan; do not save it yet.",
    }
    response = client.post(
        f"/api/agent/runs/{run_id}/followup", json=body,
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "followup-completed-001"},
    )

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["result"]["action"] == "contract_revised"
    after = repository.get_for_owner(run_id, student_id="alice")
    assert after["status"] == "queued"
    assert after["task_contract"]["revision"] == 2
    assert after["task_contract"]["constraints"] == ["Add one isolation-level self-test"]
    assert after["saved_artifacts"] == before["saved_artifacts"]
    assert after["current_plan_version"] == before["current_plan_version"]
    assert after["final_result"] is None
    assert dispatch_calls == [run_id]
    assert classifier_calls == [1]
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    assert usage["model_calls"] == 1


def test_revised_completed_run_worker_starts_a_new_checkpoint_for_current_revision(
    run_api, monkeypatch,
):
    """A completed graph checkpoint cannot suppress execution of a new revision."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.services.agent_runtime import agent_run_jobs
    from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
    from app.services.agent_runtime.agent_run_executor import (
        AgentRunExecutor, _checkpoint_thread_id,
    )
    from app.services.agent_runtime.agent_task_followup import (
        FollowupIntent, TaskFollowupClassification,
    )

    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_completed(repository)
    # The worker's trusted scope requires the production-shaped account binding;
    # this is synthetic fixture state, not a real user or authentication setup.
    with repository.mysql.engine.begin() as conn:
        conn.execute(text("""
            UPDATE agent_run SET principal_id='qa:alice', principal_source='account',
                principal_auth_epoch=1 WHERE run_id=:run_id
        """), {"run_id": run_id})
    _bind_followup_context(monkeypatch, run_id)
    _followup_usage_ledger(monkeypatch)
    prior_usage = repository.task_usage_for_owner(run_id, student_id="alice")
    monkeypatch.setattr(repository, "task_usage_for_owner", lambda *_args, **_kwargs: {
        **prior_usage, "verified": True, "reason": "fixture_usage",
        "model_calls": 1, "tool_calls": 0, "input_tokens": 0,
        "output_tokens": 0, "total_tokens": 0,
    })
    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup",
        lambda *_args, **_kwargs: TaskFollowupClassification(
            intent=FollowupIntent.SUPPLEMENT, confidence=0.98,
            patch={"add_constraints": ["Add one isolation-level self-test"]},
            clarification_question="", requested_plan_version=None,
            reason="A bounded addition to the existing plan.",
        ),
    )
    response = client.post(
        f"/api/agent/runs/{run_id}/followup",
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "revision-worker-001"},
        json={
            "task_id": run_id, "expected_revision": 1,
            "message": "Add one self-test to the current plan; do not save it yet.",
        },
    )
    assert response.status_code == 200, response.get_json()
    revised = repository.get_for_owner(run_id, student_id="alice")
    assert revised["status"] == "queued"
    assert revised["task_contract"]["revision"] == 2
    assert revised["current_plan_version"] == 1

    dispatch = AgentRunDispatchStore(repository)
    reservation = dispatch.reserve_publish(run_id)
    assert reservation["reserved"] is True, reservation
    assert dispatch.finish_publish(
        run_id, publish_token=reservation["publish_token"], published=True,
    ) == "published"
    monkeypatch.setattr(agent_run_jobs, "get_agent_run_dispatch_store", lambda: dispatch)
    monkeypatch.setattr(agent_run_jobs, "get_agent_run_repository", lambda: repository)
    monkeypatch.setattr(AgentRunExecutor, "_has_current_account", lambda *_args: True)
    monkeypatch.setattr(AgentRunExecutor, "_claim_and_account_current", lambda *_args: True)
    monkeypatch.setattr(AgentRunExecutor, "_durable_checkpointer", lambda _self: nullcontext(None))

    observed = {}
    legacy_thread_id = _checkpoint_thread_id(run_id, "alice")

    class FakeGraph:
        def __init__(self, executor, claim_token):
            self.executor = executor
            self.claim_token = claim_token

        def get_state(self, config):
            thread_id = config["configurable"]["thread_id"]
            observed["thread_id"] = thread_id
            if thread_id == legacy_thread_id:
                # This models the prior completed LangGraph state: it has no
                # next node, although the authoritative MySQL run was revised.
                return SimpleNamespace(
                    values={"run_id": run_id, "student_id": "alice", "done": True},
                    next=(),
                )
            return SimpleNamespace(values={}, next=())

        def invoke(self, initial, *, config):
            assert initial == {"run_id": run_id, "student_id": "alice", "done": False}
            current = repository.get_for_owner(run_id, student_id="alice")
            observed["contract_revision"] = current["task_contract"]["revision"]
            observed["executed"] = self.executor._advance_once(
                    run_id, "alice", self.claim_token,
            )
            return {"run_id": run_id, "student_id": "alice", "done": True}

    def build_graph(executor, _checkpointer, _run_id, _owner, claim_token):
        return FakeGraph(executor, claim_token)

    def advance(executor, claimed_run_id, owner, claim_token):
        assert (claimed_run_id, owner) == (run_id, "alice")
        current = repository.get_for_owner(run_id, student_id=owner)
        assert current["task_contract"]["revision"] == 2
        observed["claim_token"] = claim_token
        return True

    monkeypatch.setattr(AgentRunExecutor, "_build_graph", build_graph)
    monkeypatch.setattr(AgentRunExecutor, "_advance_once", advance)
    result = agent_run_jobs.agent_run_task.apply(args=[run_id], throw=True)

    assert result.failed() is False
    assert observed["thread_id"] != legacy_thread_id
    assert observed["contract_revision"] == 2
    assert observed["executed"] is True
    final = repository.get_for_owner(run_id, student_id="alice")
    assert final["task_contract"]["revision"] == 2
    assert final["current_plan_version"] == 1


def test_cancelled_run_cannot_be_resurrected_by_contract_revision(run_api):
    from app.services.agent_runtime.agent_run_store import AgentRunConflict

    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    run = repository.get_for_owner(run_id, student_id="alice")
    before_revision = run["current_contract_revision"]
    repository.request_cancel(run_id, student_id="alice")
    cancelled = repository.mark_cancelled_at_safe_point(run_id, student_id="alice")
    assert cancelled["status"] == "cancelled"

    with pytest.raises(AgentRunConflict, match="terminal or uncertain"):
        repository.revise_task_contract(
            run_id,
            student_id="alice",
            control={
                "intent": "supplement",
                "task_id": run_id,
                "expected_revision": before_revision,
                "patch": {"add_constraints": ["Do not run this cancelled task"]},
            },
            idempotency_key="cancelled-revision-001",
        )

    after = repository.get_for_owner(run_id, student_id="alice")
    assert after["status"] == "cancelled"
    assert after["current_contract_revision"] == before_revision
    assert AgentRunDispatchStore(repository).get_for_owner(
        run_id, student_id="alice",
    )["status"] == "cancelled"


def test_quick_pause_is_idempotent_and_does_not_spend_classifier_budget(run_api, monkeypatch):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    body = {"task_id": run_id, "expected_revision": 1, "message": "暂停一下"}
    headers = {"X-Test-Owner": "alice", "Idempotency-Key": "followup-pause-001"}
    first = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["data"]["result"]["action"] == "pause_requested"
    event_seq = repository.get_for_owner(run_id, student_id="alice")["last_event_seq"]
    replay = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()["data"]["result"]["action"] == "pause_requested"
    assert repository.get_for_owner(run_id, student_id="alice")["last_event_seq"] == event_seq
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    assert usage["model_calls"] == 0


def test_quick_cancel_settles_before_worker_claim_and_replay_adds_no_action(run_api, monkeypatch):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup",
        lambda *_args, **_kwargs: pytest.fail("short cancel command must not call the model"),
    )
    body = {"task_id": run_id, "expected_revision": 1,
            "message": "取消当前任务"}
    headers = {"X-Test-Owner": "alice", "Idempotency-Key": "followup-cancel-001"}
    first = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["data"]["result"]["action"] == "cancel_requested"
    event_seq = repository.get_for_owner(run_id, student_id="alice")["last_event_seq"]
    dispatch = AgentRunDispatchStore(repository)
    assert repository.get_for_owner(run_id, student_id="alice")["status"] == "cancelled"
    assert dispatch.get_for_owner(run_id, student_id="alice")["status"] == "cancelled"
    assert dispatch.claim_worker(run_id) is None

    replay = client.post(f"/api/agent/runs/{run_id}/followup", json=body, headers=headers)
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()["data"]["result"]["action"] == "cancel_requested"
    assert repository.get_for_owner(run_id, student_id="alice")["last_event_seq"] == event_seq
    assert dispatch.claim_worker(run_id) is None


def test_natural_save_request_never_writes_without_explicit_version_confirmation(
    run_api, monkeypatch,
):
    from app.services.agent_runtime.agent_task_followup import (
        FollowupIntent, TaskFollowupClassification,
    )

    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_completed(repository)
    _bind_followup_context(monkeypatch, run_id)
    _followup_usage_ledger(monkeypatch)
    # The fixture builds a completed plan without a real model. Supply the
    # verified durable usage shape that a real executor persists before finalizing.
    usage = repository.task_usage_for_owner(run_id, student_id="alice")
    monkeypatch.setattr(
        repository, "task_usage_for_owner",
        lambda *_args, **_kwargs: {
            **usage, "verified": True, "reason": "test_fixture_observed",
            "model_calls": 0, "tool_calls": 0, "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0,
        },
    )
    artifact_index = _MemoryArtifactIndex()
    monkeypatch.setattr(
        "app.services.agent_runtime.artifact_committer.get_artifact_committer",
        lambda: artifact_index,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.agent_task_followup.classify_task_followup",
        lambda *_args, **_kwargs: TaskFollowupClassification(
            intent=FollowupIntent.SAVE_REQUEST, confidence=0.97, patch=None,
            clarification_question="", requested_plan_version=None,
            reason="The user asks to save the current plan.",
        ),
    )
    body = {"task_id": run_id, "expected_revision": 1, "message": "Save this plan"}
    response = client.post(
        f"/api/agent/runs/{run_id}/followup", json=body,
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "followup-save-001"},
    )
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["data"]["result"]
    assert result["action"] == "confirm_save"
    assert result["contract_revision"] == 1
    assert result["plan_version"] == 1
    assert result["plan_sha256"]
    assert repository.get_for_owner(run_id, student_id="alice")["saved_artifacts"] == []

    explicit_save = client.post(
        f"/api/agent/runs/{run_id}/save",
        json={"contract_revision": result["contract_revision"],
              "plan_version": result["plan_version"]},
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "save-after-followup-001"},
    )
    assert explicit_save.status_code == 200, explicit_save.get_json()
    assert artifact_index.commit_calls == 1
    assert len(repository.get_for_owner(run_id, student_id="alice")["saved_artifacts"]) == 1


def test_followup_rejects_stale_revision_and_wrong_current_run(run_api, monkeypatch):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    _bind_followup_context(monkeypatch, run_id)
    stale = client.post(
        f"/api/agent/runs/{run_id}/followup",
        json={"task_id": run_id, "expected_revision": 2, "message": "six hours"},
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "followup-stale-001"},
    )
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "AGENT_RUN_STALE_REVISION"

    _bind_followup_context(monkeypatch, "run_" + "b" * 32)
    wrong_current = client.post(
        f"/api/agent/runs/{run_id}/followup",
        json={"task_id": run_id, "expected_revision": 1, "message": "six hours"},
        headers={"X-Test-Owner": "alice", "Idempotency-Key": "followup-current-002"},
    )
    assert wrong_current.status_code == 409


def test_followup_classifier_reservation_has_one_execution_owner(run_api):
    client, repository = run_api
    _install_task_schema(repository)
    run_id = _submit_open_task(repository)
    begun = repository.begin_task_followup_operation(
        run_id, student_id="alice", expected_revision=1,
        message="把计划改成每周六小时", idempotency_key="followup-claim-001",
    )
    assert begun["operation"]["status"] == "waiting_safe_point"

    first = repository.reserve_task_followup_model_call(
        run_id, student_id="alice", expected_revision=1,
        idempotency_key="followup-claim-001",
    )
    replay = repository.reserve_task_followup_model_call(
        run_id, student_id="alice", expected_revision=1,
        idempotency_key="followup-claim-001",
    )

    assert first["classification_claimed"] is True
    assert first["operation"]["status"] == "classifying"
    assert replay["classification_claimed"] is False
    assert replay["operation"]["reserved_model_calls"] == 1
