from __future__ import annotations

import pytest

from app.services.agent_runtime.agent_task_control import (
    ControlIntent,
    PlanBinding,
    ReadGrant,
    StaleTaskOutput,
    StaleTaskRevision,
    TaskBudget,
    TaskContract,
    TaskControlError,
    WriteGrant,
    assert_current_plan,
    decide_control,
    parse_control_message,
    revise_contract,
    write_is_authorized,
)


@pytest.fixture
def contract():
    return TaskContract(
        task_id="run_0123456789abcdef0123456789abcdef",
        revision=3,
        goal="制定八周成都后端求职计划",
        deliverables=("每周计划", "来源与验收依据"),
        constraints=("每周可投入 8 小时", "只读取本人明确授权的资料"),
        acceptance_criteria=("共八周", "每周任务可验证"),
        reads=(ReadGrant("career://me/resume", "resume-v7"),),
        writes=(WriteGrant(
            "artifact.save", "artifact_plan_v1", "sha256:abc123", "confirm_v3_01", 3,
        ),),
        budget=TaskBudget(
            max_model_calls=16,
            max_tool_calls=20,
            max_input_tokens=80_000,
            max_output_tokens=12_000,
            max_call_input_tokens=24_000,
            max_call_output_tokens=4_000,
            max_retries=2,
        ),
    )


def test_contract_revision_replaces_constraint_and_expires_version_bound_write_grant(contract):
    message = parse_control_message({
        "intent": "supplement",
        "task_id": contract.task_id,
        "expected_revision": 3,
        "patch": {"constraint_updates": [{"current": "每周可投入 8 小时", "replacement": "每周可投入 6 小时"}]},
    }, current=contract)

    revision = revise_contract(
        contract,
        message,
        current_plan_version=2,
        task_status="running",
        active_step_status="completed",
    )

    assert revision.changed is True
    assert revision.contract.revision == 4
    assert revision.contract.constraints == ("每周可投入 6 小时", "只读取本人明确授权的资料")
    assert revision.contract.budget == contract.budget
    assert revision.contract.reads == contract.reads
    assert revision.contract.writes == ()
    assert revision.supersedes_plan_version == 2
    assert revision.invalidates_prior_result is True
    assert revision.preserves_completed_steps is True
    assert contract.constraints == ("每周可投入 8 小时", "只读取本人明确授权的资料")


def test_old_worker_result_cannot_publish_after_contract_or_plan_revision(contract):
    old = PlanBinding(contract.task_id, contract_revision=3, plan_version=2)
    revised = TaskContract(
        task_id=contract.task_id,
        revision=4,
        goal=contract.goal,
        deliverables=contract.deliverables,
        constraints=("每周可投入 6 小时",),
        acceptance_criteria=contract.acceptance_criteria,
        reads=contract.reads,
        writes=(),
        budget=contract.budget,
    )

    with pytest.raises(StaleTaskOutput):
        assert_current_plan(revised, old, current_plan_version=2)
    with pytest.raises(StaleTaskOutput):
        assert_current_plan(revised, PlanBinding(contract.task_id, 4, 2), current_plan_version=3)

    assert_current_plan(revised, PlanBinding(contract.task_id, 4, 3), current_plan_version=3)


def test_stale_control_message_cannot_rewrite_current_contract(contract):
    with pytest.raises(StaleTaskRevision):
        parse_control_message({
            "intent": "revise_goal",
            "task_id": contract.task_id,
            "expected_revision": 2,
            "patch": {"goal": "旧目标"},
        }, current=contract)


def test_progress_query_reads_latest_task_even_from_a_stale_card(contract):
    message = parse_control_message({
        "intent": "progress_query",
        "task_id": contract.task_id,
        "expected_revision": 2,
    }, current=contract)
    assert message.expected_revision == contract.revision
    assert decide_control(message, task_status="running", active_step_status="running").action == "read_progress"


def test_incomplete_contract_can_wait_for_input_but_cannot_start_planning(contract):
    draft = TaskContract(
        task_id=contract.task_id,
        revision=0,
        goal="制定职业计划",
        deliverables=(),
        constraints=(),
        acceptance_criteria=(),
        reads=contract.reads,
        writes=(),
        budget=contract.budget,
    )
    assert draft.missing_fields == ("deliverables", "acceptance_criteria")
    with pytest.raises(TaskControlError, match="needs user input"):
        draft.require_plan_ready()

    message = parse_control_message({
        "intent": "supplement",
        "task_id": draft.task_id,
        "expected_revision": draft.revision,
        "patch": {
            "deliverables": ["八周计划"],
            "acceptance_criteria": ["每周安排可检查"],
        },
    }, current=draft)
    revised = revise_contract(
        draft,
        message,
        current_plan_version=None,
        task_status="waiting_input",
        active_step_status=None,
    )
    assert revised.contract.missing_fields == ()
    revised.contract.require_plan_ready()


@pytest.mark.parametrize("patch", [
    {"writes": [{"action": "artifact.save"}]},
    {"budget": {"max_model_calls": 999}},
    {"authorization": {"external_send": True}},
])
def test_model_control_patch_cannot_grant_write_or_raise_budget(contract, patch):
    with pytest.raises(TaskControlError):
        parse_control_message({
            "intent": "supplement",
            "task_id": contract.task_id,
            "expected_revision": contract.revision,
            "patch": patch,
        }, current=contract)


def test_intent_shapes_preserve_task_identity_and_separate_new_tasks(contract):
    with pytest.raises(TaskControlError):
        parse_control_message({
            "intent": "supplement",
            "task_id": contract.task_id,
            "expected_revision": 3,
            "patch": {"goal": "悄悄扩大目标"},
        }, current=contract)
    with pytest.raises(TaskControlError):
        parse_control_message({
            "intent": "revise_goal",
            "task_id": contract.task_id,
            "expected_revision": 3,
            "patch": {"constraint_updates": [{"current": "每周可投入 8 小时", "replacement": "周投入改为 6 小时"}]},
        }, current=contract)
    with pytest.raises(TaskControlError):
        parse_control_message({
            "intent": "progress_query",
            "task_id": contract.task_id,
            "expected_revision": 3,
            "patch": {"add_constraints": ["错误地修改进度查询"]},
        }, current=contract)

    message = parse_control_message({"intent": "new_task"}, current=contract)
    assert message.intent is ControlIntent.NEW_TASK
    assert decide_control(message, task_status="running", active_step_status="running").action == "create_new_task"


def test_pause_and_cancel_are_requests_not_rollback_or_retry(contract):
    pause = parse_control_message({
        "intent": "pause",
        "task_id": contract.task_id,
        "expected_revision": contract.revision,
    }, current=contract)
    pause_decision = decide_control(pause, task_status="running", active_step_status="running")
    assert pause_decision.action == "request_pause_at_safe_point"
    assert pause_decision.request_safe_point is True
    assert pause_decision.preserve_completed_effects is True

    cancel = parse_control_message({
        "intent": "cancel",
        "task_id": contract.task_id,
        "expected_revision": contract.revision,
    }, current=contract)
    cancel_decision = decide_control(cancel, task_status="running", active_step_status="needs_reconciliation")
    assert cancel_decision.action == "request_cancel_and_reconcile"
    assert cancel_decision.allow_retry_without_reconciliation is False
    assert cancel_decision.preserve_completed_effects is True


def test_pending_uncertain_step_blocks_revision_until_reconciled(contract):
    message = parse_control_message({
        "intent": "supplement",
        "task_id": contract.task_id,
        "expected_revision": contract.revision,
        "patch": {"constraint_updates": [{"current": "每周可投入 8 小时", "replacement": "每周可投入 6 小时"}]},
    }, current=contract)
    decision = decide_control(message, task_status="running", active_step_status="needs_reconciliation")
    assert decision.action == "wait_for_step_safe_point"
    assert decision.request_safe_point is True
    assert decision.allow_retry_without_reconciliation is False
    with pytest.raises(TaskControlError):
        revise_contract(
            contract,
            message,
            current_plan_version=2,
            task_status="running",
            active_step_status="needs_reconciliation",
        )


def test_write_confirmation_is_bound_to_exact_revision_target_and_version(contract):
    assert write_is_authorized(
        contract,
        action="artifact.save",
        target="artifact_plan_v1",
        target_version="sha256:abc123",
    )
    assert not write_is_authorized(
        contract,
        action="artifact.save",
        target="artifact_plan_v1",
        target_version="sha256:changed",
    )
    assert not write_is_authorized(
        contract,
        action="external_send",
        target="email:someone@example.invalid",
        target_version="message-v1",
    )


def test_noop_revision_does_not_invalidate_plan_or_change_revision(contract):
    message = parse_control_message({
        "intent": "supplement",
        "task_id": contract.task_id,
        "expected_revision": contract.revision,
        "patch": {"constraint_updates": [{"current": contract.constraints[0], "replacement": contract.constraints[0]}]},
    }, current=contract)
    revision = revise_contract(
        contract,
        message,
        current_plan_version=2,
        task_status="paused",
        active_step_status=None,
    )
    assert revision.changed is False
    assert revision.contract is contract
    assert revision.contract.revision == contract.revision
    assert revision.supersedes_plan_version is None
    assert revision.invalidates_prior_result is False
