from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from app.services.agent_runtime.agent_task_control import TaskBudget, initial_task_contract  # noqa: E402
from app.services.agent_runtime.agent_task_followup import (  # noqa: E402
    FollowupClassifierError,
    FollowupIntent,
    classify_task_followup,
    parse_followup_classification,
    quick_control_intent,
)


TASK_ID = "run_" + "a" * 32
CONTRACT = initial_task_contract(
    TASK_ID, "Create an eight-week backend study plan",
    TaskBudget(
        max_model_calls=16, max_tool_calls=32,
        max_input_tokens=120_000, max_output_tokens=24_000,
        max_call_input_tokens=16_000, max_call_output_tokens=4_000,
        max_retries=1,
    ),
)


def _raw(**updates):
    result = {
        "intent": "supplement",
        "confidence": 0.96,
        "ambiguous": False,
        "explicit_independent_task": False,
        "patch": {
            "goal": "", "deliverables": [], "acceptance_criteria": [],
            "constraint_updates": [], "add_constraints": ["Six hours per week"],
            "remove_constraints": [],
        },
        "clarification_question": "",
        "requested_plan_version": 0,
        "reason": "The user adds a weekly time constraint.",
    }
    result.update(updates)
    return result


def test_followup_patch_is_revision_only_and_cannot_change_permission_or_budget():
    parsed = parse_followup_classification(_raw(), contract=CONTRACT)
    assert parsed.intent is FollowupIntent.SUPPLEMENT
    assert parsed.patch == {"add_constraints": ["Six hours per week"]}

    forbidden = _raw(patch={
        "goal": "", "deliverables": [], "acceptance_criteria": [],
        "constraint_updates": [], "add_constraints": [], "remove_constraints": [],
        "writes": [{"action": "profile.update"}],
    })
    with pytest.raises(FollowupClassifierError):
        parse_followup_classification(forbidden, contract=CONTRACT)


def test_ambiguous_or_low_confidence_followup_asks_instead_of_mutating():
    parsed = parse_followup_classification(
        _raw(confidence=0.51, clarification_question="Do you mean Saturdays?"),
        contract=CONTRACT,
    )
    assert parsed.intent is FollowupIntent.CLARIFY
    assert parsed.patch is None
    assert parsed.clarification_question == "Do you mean Saturdays?"


def test_new_task_requires_explicit_independent_request_and_high_confidence():
    proposal = _raw(intent="new_task", confidence=0.99,
                    explicit_independent_task=False, patch={
                        "goal": "", "deliverables": [], "acceptance_criteria": [],
                        "constraint_updates": [], "add_constraints": [],
                        "remove_constraints": [],
                    })
    assert parse_followup_classification(proposal, contract=CONTRACT).intent is FollowupIntent.CLARIFY
    proposal["explicit_independent_task"] = True
    assert parse_followup_classification(proposal, contract=CONTRACT).intent is FollowupIntent.NEW_TASK


def test_save_request_only_routes_to_exact_version_confirmation():
    proposal = _raw(intent="save_request", patch={
        "goal": "", "deliverables": [], "acceptance_criteria": [],
        "constraint_updates": [], "add_constraints": [], "remove_constraints": [],
    }, requested_plan_version=3)
    parsed = parse_followup_classification(proposal, contract=CONTRACT)
    assert parsed.intent is FollowupIntent.SAVE_REQUEST
    assert parsed.requested_plan_version == 3
    assert parsed.patch is None


def test_quick_pause_and_cancel_commands_are_narrow_and_ambiguous_text_uses_model():
    assert quick_control_intent("暂停一下") is FollowupIntent.PAUSE
    assert quick_control_intent("取消当前任务") is FollowupIntent.CANCEL
    assert quick_control_intent("别做了") is None
    assert quick_control_intent("暂停后改成六小时") is None


def test_classifier_uses_strict_schema_and_current_contract_and_run_state(monkeypatch):
    captured = {}

    class FakeClient:
        def chat(self, **kwargs):
            captured.update(kwargs)
            return json.dumps(_raw(), ensure_ascii=False)

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(
        "app.services.agent_runtime.model_router.route_model",
        lambda name: {"model": "test-classifier", "temperature": 0, "max_tokens": 700},
    )
    result = classify_task_followup(
        "每周只有六小时",
        contract=CONTRACT,
        run={"run_id": TASK_ID, "status": "paused", "current_plan": {
            "plan_version": 2, "goal_summary": "Eight-week plan",
            "steps": [{"goal": "Read role evidence", "status": "completed"}],
        }},
    )

    assert result.intent is FollowupIntent.SUPPLEMENT
    schema = captured["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    user_payload = json.loads(captured["messages"][1]["content"])
    assert user_payload["current_task_contract"]["latest_constraints"] == []
    assert user_payload["current_task_state"]["plan_version"] == 2
    assert user_payload["user_followup_message"] == "每周只有六小时"
