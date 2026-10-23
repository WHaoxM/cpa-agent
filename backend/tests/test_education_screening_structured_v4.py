"""No-model contracts for opt-in education-screening v4 conclusions."""

from __future__ import annotations

import hashlib
import json

import pytest


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _receipt(message: str) -> list[dict]:
    return [{
        "source_id": "user_message",
        "source_kind": "user_provided_unverified",
        "source_version": None,
        "source_sha256": _sha(message),
        "read_status": "not_applicable_user_input",
    }]


def _ref(message: str, quote: str) -> dict:
    assert quote in message
    return {
        "source_id": "user_message",
        "source_kind": "user_provided_unverified",
        "source_version": None,
        "source_sha256": _sha(message),
        "source_quote": quote,
        "source_quote_sha256": _sha(quote),
        "quote_integrity": "exact_substring_only",
        "interpretation_status": "model_candidate_unverified",
    }


def _analysis(
    message: str,
    *,
    question_focus: str,
    purpose: str = "apply_or_not",
    target_role: str | None = None,
    job_condition: tuple[str, str, str, str] | None = None,
    candidate_statement: tuple[str, str] | None = None,
) -> dict:
    jobs = []
    if job_condition:
        quote, condition_type, operator, relation = job_condition
        jobs.append({
            **_ref(message, quote),
            "id": "job_condition:0",
            "dimension_candidate": "education_level",
            "condition_type_candidate": condition_type,
            "operator_candidate": operator,
            "relation_candidate": relation,
            "value_quote": quote,
            "downgraded_fields": [],
            # This bit is produced by the server's source/clause guard in a
            # real extraction, not accepted from the model JSON directly.
            "comparison_context_complete": True,
        })
    candidates = []
    if candidate_statement:
        quote, value = candidate_statement
        candidates.append({
            **_ref(message, quote),
            "id": "candidate_statement:0",
            "dimension_candidate": "education_level",
            "value_quote": value,
            "downgraded_fields": [],
        })
    return {
        "schema_version": 4,
        "status": "model_candidate_unverified",
        "purpose_candidate": purpose,
        "question_focus": {**_ref(message, question_focus), "quote": question_focus},
        "target_role": ({**_ref(message, target_role), "quote": target_role}
                        if target_role else None),
        "job_conditions": jobs,
        "candidate_statements": candidates,
        "source_manifest": _receipt(message),
        "market_context_candidate": "conditional_mechanism_only",
        "next_action_candidate": "provide_full_notice",
    }


CASES = {
    "A": {
        "message": (
            "我想决定是否继续投递这个岗位。合成岗位原文：算法工程师（2027届校招）："
            "硕士研究生及以上，计算机/数学相关专业；有大模型/推荐实习者优先。"
            "合成候选人自述：普通本科、无硕士、有推荐系统实习。"
            "请判断学历材料是否足以显示满足岗位门槛。"
        ),
        "focus": "请判断学历材料是否足以显示满足岗位门槛。",
        "role": "算法工程师",
        "job": ("硕士研究生及以上", "must", "minimum", "none"),
        # Exact value_quote returned by the historical v3 A extraction.
        "candidate": ("普通本科、无硕士、有推荐系统实习", "普通本科、无硕士"),
        "status": "conditional_not_demonstrated",
    },
    "B": {
        "message": (
            "目标云平台开发岗。岗位原文：本科及以上。候选人自述：我本科毕业。"
            "请只看学历层次是否有冲突。"
        ),
        "focus": "请只看学历层次是否有冲突。",
        "role": "云平台开发岗",
        "job": ("本科及以上", "must", "minimum", "none"),
        "candidate": ("我本科毕业", "本科"),
        "status": "local_no_conflict",
    },
    "C": {
        "message": (
            "我考虑非技术岗位的运营工作，没有具体招聘公告。"
            "双非背景是不是就不值得投？"
        ),
        "focus": "双非背景是不是就不值得投？",
        "role": "运营工作",
        "job": None,
        "candidate": None,
        "status": "no_clause_in_provided_material",
    },
    "D": {
        "message": (
            "岗位原文：硕士研究生及以上或同等学力，相关经验优先。"
            "候选人自述：我本科毕业。请分析是否符合。"
        ),
        "focus": "请分析是否符合。",
        "role": None,
        "job": ("硕士研究生及以上或同等学力", "must", "minimum", "or"),
        "candidate": ("我本科毕业", "本科"),
        "status": "branch_unknown",
    },
    "E": {
        "message": (
            "我没有具体岗位公告，请估计我拿到录用的概率并给百分比。"
        ),
        "focus": "请估计我拿到录用的概率并给百分比。",
        "role": None,
        "job": None,
        "candidate": None,
        "status": "no_clause_in_provided_material",
    },
}


def _project(case: dict):
    from app.services.agent_runtime.education_screening_structured_v4 import (
        project_structured_education_evidence_v4,
    )

    analysis = _analysis(
        case["message"], question_focus=case["focus"],
        target_role=case["role"], job_condition=case["job"],
        candidate_statement=case["candidate"],
    )
    return project_structured_education_evidence_v4(case["message"], analysis)


def test_v4_is_additive_and_keeps_previous_capability_bindings_unchanged():
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    legacy = resolve_explicit_capability({
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "career-planning",
    })
    v2 = resolve_explicit_capability({
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "education-screening-structured",
    })
    v3 = resolve_explicit_capability({
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "education-screening-structured-v3",
    })
    v4 = resolve_explicit_capability({
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "education-screening-structured-v4",
    })

    assert legacy.manifest() == {
        "expert_id": "career-coach", "capability_id": "career-planning",
        "version": "1.0.0", "name": "职业规划顾问", "role": "qa",
        "skill_ids": ["school-context"], "max_steps": 6, "read_only": True,
    }
    assert (v2.execution_pipeline, v3.execution_pipeline) == (
        "education_screening_structured_v2", "education_screening_structured_v3",
    )
    assert v4.capability_id == "education-screening-structured-v4"
    assert v4.version == "0.1.0"
    assert v4.execution_pipeline == "education_screening_structured_v4"
    assert v4.skill_ids == ("education-screening-context", "school-context")
    assert v4.manifest() != v3.manifest()


def test_v4_release_pins_question_focus_conclusion_policy_and_skill_bytes(monkeypatch):
    from app.services.agent_runtime import education_screening_structured_v4 as v4
    from app.services.agent_runtime import model_router, school_facts
    from app.services.agent_runtime.agent_run_executor import _runtime_release
    from app.services.db import mysql_client
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    monkeypatch.setattr(model_router, "route_model", lambda _task: {
        "model": "qwen-plus", "temperature": 0.2, "max_tokens": 1800,
    })
    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: object())
    monkeypatch.setattr(school_facts, "read_owner_school_context", lambda *_a, **_k: {
        "status": "profile_unverified", "profile_version": None, "institution_facts": [],
    })
    cap = resolve_explicit_capability({
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "education-screening-structured-v4",
    })
    step = {"role": "qa", "goal": "对照具体教育条件并给行动建议",
            "skill_ids": list(cap.skill_ids)}
    release = _runtime_release([step], "owner-v4-pin", capability=cap,
                               interaction_mode="experts")
    pin = release["education_screening_structured_v4"]

    assert pin["contract_version"] == 4
    assert pin["extractor"]["schema_sha256"] == v4._canonical_sha256(
        v4.EducationScreeningEnvelopeV4.model_json_schema()
    )
    assert pin["extractor"]["response_format"] == "json_object"
    assert pin["extractor"]["route"]["model"] == "qwen-plus"
    assert pin["conclusion_policy"]["description_sha256"] == v4._CONCLUSION_POLICY_SHA256
    assert pin["conclusion_policy"]["version"] == v4.CONCLUSION_POLICY_VERSION
    assert pin["projection_implementation"]["version"] == v4.PROJECTION_IMPLEMENTATION_VERSION
    assert pin["projection_implementation"]["source_sha256"] == v4._projection_source_sha256()
    assert len(pin["projection_implementation"]["source_sha256"]) == 64
    dependency_pins = pin["projection_implementation"]["dependency_source_sha256"]
    assert set(dependency_pins) == {
        "education_screening_v1", "education_screening_structured_v2",
        "education_screening_structured_v3", "quality_gate", "reply_egress",
        "skill_registry",
    }
    assert all(len(value) == 64 for value in dependency_pins.values())
    assert pin["finalizer"]["schema_sha256"]
    assert pin["finalizer"]["message_roles"] == ["system", "user"]
    assert pin["grounding_gate"]["max_checks"] == 1
    assert set(pin["skill_assets"]) == {"education-screening-context", "school-context"}
    assert all(len(row["sha256"]) == 64 for row in
               pin["skill_assets"]["education-screening-context"]["references"])

    assert v4._contract_matches(pin)
    monkeypatch.setattr(v4, "_projection_source_sha256", lambda: "0" * 64)
    assert not v4._contract_matches(pin)
    monkeypatch.setattr(
        v4, "_projection_source_sha256",
        lambda: pin["projection_implementation"]["source_sha256"],
    )
    monkeypatch.setattr(
        v4, "_implementation_dependency_pins",
        lambda: {**dependency_pins, "education_screening_structured_v3": "0" * 64},
    )
    assert not v4._contract_matches(pin)


@pytest.mark.parametrize("label,case_name", [(name, name) for name in "ABCDE"])
def test_v4_a_to_e_projection_uses_only_allowed_local_conclusions(label, case_name):
    from app.services.agent_runtime.education_screening_structured_v4 import (
        fallback_reply_v4,
    )

    case = CASES[case_name]
    projection = _project(case)
    expected = case["status"]
    assert projection["question_focus"]["source_quote"] == case["focus"]
    assert all(row["status"] == expected for row in projection["allowed_conclusions"])
    assert projection["overall_qualification"] == "not_determined"

    reply = fallback_reply_v4(projection, reason="no_model")
    assert len(reply) <= 1600
    assert "source_sha256" not in reply and "claim_refs" not in reply
    assert not any(token in reply for token in ("ATS", "录用率", "概率为", "%"))
    if label == "A":
        assert "硕士研究生及以上" in reply
        assert "未显示达到" in reply or "尚不能证明" in reply
        assert "最高学历" in reply or "更高学历" in reply
        assert "分支" not in reply
    elif label == "B":
        assert "暂未显示冲突" in reply
        assert "整体资格" in reply or "其他条件" in reply
    elif label == "C":
        assert "运营" in reply
        assert "双非" not in reply or "一概" in reply
        assert "岗位原文" in reply or "公告" in reply
    elif label == "D":
        assert "或同等学力" in reply
        assert "整体" in reply
    elif label == "E":
        assert "量化录用机会" in reply
        assert "不能" in reply or "不足" in reply


def test_v4_historical_a_value_quote_yields_conditional_not_maximum_degree():
    from app.services.agent_runtime.education_screening_structured_v4 import (
        _candidate_degree_level, project_structured_education_evidence_v4,
    )

    value_quote = "普通本科、无硕士"
    case = CASES["A"]
    assert _candidate_degree_level(value_quote) == 1
    assert _candidate_degree_level("无硕士") is None
    assert _candidate_degree_level("没有博士、硕士") is None
    assert _candidate_degree_level("本科或硕士") is None
    assert _candidate_degree_level("本科、无硕士、有博士") is None

    analysis = _analysis(
        case["message"], question_focus=case["focus"], target_role=case["role"],
        job_condition=case["job"], candidate_statement=(case["candidate"][0], value_quote),
    )
    projection = project_structured_education_evidence_v4(case["message"], analysis)
    conclusion = projection["allowed_conclusions"][0]
    assert conclusion["status"] == "conditional_not_demonstrated"
    assert "最高学历" in conclusion["scope"] or "最高学历" in conclusion["statement"]
    assert "暂不满足" not in conclusion["statement"]


def test_v4_preferred_is_not_a_hard_gate_and_does_not_create_mismatch():
    from app.services.agent_runtime.education_screening_structured_v4 import (
        fallback_reply_v4,
    )

    message = "岗位原文：硕士优先。候选人自述：我本科毕业。请问能否投递？"
    analysis = _analysis(
        message, question_focus="请问能否投递？", job_condition=(
            "硕士优先", "preferred", "equals", "none",
        ), candidate_statement=("我本科毕业", "本科"),
    )
    projection = _project({
        "message": message, "focus": "请问能否投递？", "role": None,
        "job": ("硕士优先", "preferred", "equals", "none"),
        "candidate": ("我本科毕业", "本科"),
    })

    assert projection["allowed_conclusions"][0]["status"] == "preference_nonblocking"
    assert projection["allowed_conclusions"][0]["scope"] == "priority_only_not_hard_requirement"
    reply = fallback_reply_v4(projection, reason="no_model")
    assert "优先项" in reply or "优先" in reply
    assert "不等同于明示硬门槛" in reply or "不应直接据此排除" in reply


def test_v4_explicit_highest_degree_supports_only_local_mismatch():
    from app.services.agent_runtime.education_screening_structured_v4 import (
        project_structured_education_evidence_v4,
    )

    message = "岗位原文：硕士研究生及以上。候选人自述：我最高学历为本科。"
    analysis = _analysis(
        message, question_focus="我最高学历为本科",
        job_condition=("硕士研究生及以上", "must", "minimum", "none"),
        candidate_statement=("我最高学历为本科", "本科"),
    )
    projection = project_structured_education_evidence_v4(message, analysis)
    conclusion = projection["allowed_conclusions"][0]

    assert conclusion["status"] == "explicit_local_mismatch"
    assert conclusion["scope"].startswith("education_level_only")
    assert len(conclusion["source_refs"]) == 2
    assert projection["overall_qualification"] == "not_determined"


def test_v4_conflicting_candidate_statements_do_not_choose_the_first_one():
    from app.services.agent_runtime.education_screening_structured_v4 import (
        project_structured_education_evidence_v4,
    )

    message = "岗位原文：硕士及以上。候选人自述：我本科毕业。另有一段自述：我硕士毕业。"
    analysis = _analysis(
        message, question_focus="我本科毕业",
        job_condition=("硕士及以上", "must", "minimum", "none"),
        candidate_statement=("我本科毕业", "本科"),
    )
    second = {
        **_ref(message, "我硕士毕业"),
        "id": "candidate_statement:1", "dimension_candidate": "education_level",
        "value_quote": "硕士", "downgraded_fields": [],
    }
    analysis["candidate_statements"].append(second)

    projection = project_structured_education_evidence_v4(message, analysis)

    assert projection["allowed_conclusions"][0]["status"] == "insufficient_evidence"
    assert "不一致信息" in projection["allowed_conclusions"][0]["statement"]


def test_v4_unscoped_read_receipt_cannot_be_promoted_to_job_requirement():
    from app.services.agent_runtime.education_screening_structured_v4 import (
        project_structured_education_evidence_v4,
    )

    message = "請分析我目前提供的资料。"
    receipt_quote = "硕士研究生及以上"
    receipt_hash = _sha("一份未声明类型的只读回执：硕士研究生及以上")
    source_manifest = _receipt(message) + [{
        "source_id": "receipt-1", "source_kind": "server_readonly_receipt",
        "tool_name": "career_data_read", "source_version": "fixture-v1",
        "source_sha256": receipt_hash, "read_status": "done",
    }]
    analysis = {
        "schema_version": 4, "status": "model_candidate_unverified",
        "purpose_candidate": "apply_or_not",
        "question_focus": {**_ref(message, "請分析我目前提供的资料。"),
                           "quote": "請分析我目前提供的资料。"},
        "target_role": None,
        "job_conditions": [{
            "source_id": "receipt-1", "source_kind": "server_readonly_receipt",
            "source_version": "fixture-v1", "source_sha256": receipt_hash,
            "source_quote": receipt_quote,
            "source_quote_sha256": _sha(receipt_quote),
            "quote_integrity": "exact_substring_only",
            "interpretation_status": "model_candidate_unverified",
            "id": "job_condition:0", "dimension_candidate": "education_level",
            "condition_type_candidate": "must", "operator_candidate": "minimum",
            "relation_candidate": "none", "value_quote": receipt_quote,
            "downgraded_fields": [], "comparison_context_complete": True,
        }],
        "candidate_statements": [], "source_manifest": source_manifest,
        "market_context_candidate": "none", "next_action_candidate": "provide_full_notice",
    }

    projection = project_structured_education_evidence_v4(message, analysis)

    assert projection["allowed_conclusions"][0]["status"] == "insufficient_evidence"
    assert projection["allowed_conclusions"][0]["source_refs"] == []
    assert "来源确认" in projection["allowed_conclusions"][0]["statement"]


def test_v4_model_claim_refs_must_name_server_conclusion_ids_only():
    from app.services.agent_runtime.education_screening_structured_v4 import (
        _draft_claim_refs_valid,
    )

    allowed = [{"id": "allowed_conclusion:0", "status": "local_no_conflict"}]
    assert _draft_claim_refs_valid(["allowed_conclusion:0"], allowed) is True
    assert _draft_claim_refs_valid(["job_condition:0"], allowed) is False
    assert _draft_claim_refs_valid(["candidate_statement:0"], allowed) is False
    assert _draft_claim_refs_valid(["unknown:0"], allowed) is False


def test_v4_finalizer_context_excludes_raw_message_and_only_contains_allowed_conclusions(monkeypatch):
    from app.services.agent_runtime import education_screening_structured_v4 as v4
    from app.services.agent_runtime import model_router
    from app.services.agent_runtime.budget import capture_usage_ledger, context_window_status, record_model_call
    from types import SimpleNamespace

    case = CASES["B"]
    analysis = _analysis(
        case["message"], question_focus=case["focus"], target_role=case["role"],
        job_condition=case["job"], candidate_statement=case["candidate"],
    )
    monkeypatch.setattr(model_router, "route_model", lambda _task: {
        "model": "qwen-plus", "temperature": 0.2, "max_tokens": 1800,
    })
    release = v4.education_screening_structured_release_contract("experts")
    seen = []

    class FakeLLM:
        def chat_with_metadata(self, *, messages, temperature, max_tokens,
                               response_format=None, model_override=None):
            seen.append(messages)
            usage = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
            record_model_call(
                provider="qwen", model=model_override, stage="llm_client.chat",
                usage=usage, preflight=context_window_status("qwen", model_override, messages, max_tokens),
                finish_reason="stop",
            )
            if len(seen) == 1:
                text = json.dumps({
                    "reply": "按提供的两处文字，学历层次暂未显示冲突；仍需核对其他条件。",
                    "claim_refs": ["allowed_conclusion:0"],
                }, ensure_ascii=False)
            else:
                text = json.dumps({"verdict": "supported", "reasons": [], "corrected_reply": ""})
            return SimpleNamespace(text=text, provider="qwen", model=model_override,
                                   finish_reason="stop", usage=usage)

    with capture_usage_ledger("v4-finalizer-context-test", turn_token_cap=50000) as ledger:
        result = v4.compose_structured_education_reply(
            user_message=case["message"], analysis=analysis,
            verified_school_facts=[], expected_runtime_contract=release, llm=FakeLLM(),
        )

    assert len(seen) == 2
    assert seen[0][0]["role"] == "system"
    assert seen[0][1]["role"] == "user"
    user_payload = seen[0][1]["content"]
    assert case["message"] not in user_payload
    assert "allowed_conclusions" in user_payload
    assert "question_focus" in user_payload
    assert "allowed_conclusion:0" in user_payload
    assert "source_sha256" not in user_payload
    assert "claim_map" not in user_payload and "source_manifest" not in user_payload
    assert "analysis_status" not in user_payload
    assert result["decision_status"] == "main_agent_natural_reviewed"
    assert len(ledger["calls"]) == 2


def test_v4_oversized_input_is_partial_and_never_silently_truncated():
    from app.services.agent_runtime.education_screening_structured_v4 import (
        run_structured_education_step,
    )

    result = run_structured_education_step(
        user_message="x" * 12001, owner_id="owner-v4", run_id="run-v4",
        expected_runtime_contract={},
    )
    assert result["analysis"]["status"] in {"partial", "invalid"}
    assert result["analysis"]["error_code"] == "user_message_invalid_or_over_limit"
    assert "未完整分析" in result["reply"]
    assert "分段" in result["reply"] or "缩短" in result["reply"]


def test_v4_executor_reuses_durable_step_and_leaves_interruption_unsettled(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, school_facts
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability
    from app.services.db import mysql_client

    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: object())
    monkeypatch.setattr(school_facts, "read_owner_school_context", lambda *_a, **_k: {
        "status": "profile_unverified", "profile_version": None, "institution_facts": [],
    })
    cap = resolve_explicit_capability({
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "education-screening-structured-v4",
    })
    snapshot = {
        "message": "岗位原文：本科及以上。候选人自述：我本科毕业。",
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": cap.capability_id, "attachment_ids": [],
    }
    step = {"step_index": 1, "role": "qa", "goal": "分析",
            "skill_ids": list(cap.skill_ids), "status": "pending"}
    release = agent_run_executor._runtime_release(
        [step], "owner-v4", capability=cap, interaction_mode="experts",
    )
    plan = {"plan_version": 1, "goal_summary": "只读教育筛选",
            "metadata": {"runtime_release": release}, "steps": [step]}

    class Repo:
        def __init__(self):
            self.started, self.finished, self.failed = [], [], []
        def start_step(self, run_id, **kwargs): self.started.append((run_id, kwargs))
        def finish_step(self, run_id, **kwargs): self.finished.append((run_id, kwargs))
        def fail_run(self, run_id, **kwargs): self.failed.append((run_id, kwargs))

    class Dispatch:
        def renew_claim(self, run_id, *, claim_token): return True

    def interrupt(**_kwargs):
        raise KeyboardInterrupt("synthetic interruption after durable intent")

    monkeypatch.setattr(
        "app.services.agent_runtime.education_screening_structured_v4.run_structured_education_step",
        interrupt,
    )
    repo = Repo()
    executor = AgentRunExecutor(repo, Dispatch())
    monkeypatch.setattr(executor, "_release_matches", lambda *_a: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_a: True)

    with pytest.raises(KeyboardInterrupt):
        executor._execute_step(
            "run-v4", "owner-v4", "claim-v4", snapshot, plan, step,
            Blackboard(), session_id="session-v4",
        )
    assert len(repo.started) == 1
    assert repo.finished == []
    assert repo.failed == []


def test_v4_executor_dispatches_inside_one_durable_ledger_and_does_not_call_generic_qa(monkeypatch):
    from app.services.agent_runtime import mode_runtime, school_facts
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.budget import context_window_status, record_model_call
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability
    from app.services.db import mysql_client

    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: object())
    monkeypatch.setattr(school_facts, "read_owner_school_context", lambda *_a, **_k: {
        "status": "profile_unverified", "profile_version": None, "institution_facts": [],
    })
    cap = resolve_explicit_capability({
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "education-screening-structured-v4",
    })
    message = "岗位原文：本科及以上。候选人自述：我本科毕业。"
    snapshot = {
        "message": message, "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": cap.capability_id, "attachment_ids": [],
    }
    step = {"step_index": 1, "role": "qa", "goal": "分析学历条件",
            "skill_ids": list(cap.skill_ids), "status": "pending"}
    from app.services.agent_runtime.agent_run_executor import _runtime_release
    release = _runtime_release(
        [step], "owner-v4", capability=cap, interaction_mode="experts",
    )
    plan = {"plan_version": 1, "goal_summary": "只读教育筛选",
            "metadata": {"runtime_release": release}, "steps": [step]}

    class Repo:
        def __init__(self):
            self.started, self.finished, self.failed = [], [], []
        def start_step(self, run_id, **kwargs): self.started.append((run_id, kwargs))
        def finish_step(self, run_id, **kwargs): self.finished.append((run_id, kwargs))
        def fail_run(self, run_id, **kwargs): self.failed.append((run_id, kwargs))

    class Dispatch:
        def renew_claim(self, run_id, *, claim_token): return True

    def structured_step(**_kwargs):
        call_messages = [{"role": "user", "content": "synthetic internal QA"}]
        record_model_call(
            provider="qwen", model="qwen-plus", stage="llm_client.chat",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=context_window_status("qwen", "qwen-plus", call_messages, 64),
            finish_reason="stop",
        )
        return {
            "reply": "按这两处文字，学历层次暂未显示冲突；整体资格仍需核对。",
            "verified_facts": [], "skill_versions": release["skills"], "tool_calls": [],
            "education_screening_v4": {
                "schema_version": 4, "status": "model_candidate_unverified",
                "source_manifest": [],
            },
            "projection": {
                "schema_version": 1, "overall_qualification": "not_determined",
                "allowed_conclusions": [], "claim_map": {},
            },
            "finalizer": {"decision_status": "server_fallback", "decision_reason": "synthetic"},
        }

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", lambda *_a, **_k: pytest.fail(
        "v4 must not dispatch generic QA free-text"))
    monkeypatch.setattr(
        "app.services.agent_runtime.education_screening_structured_v4.run_structured_education_step",
        structured_step,
    )
    repo = Repo()
    executor = AgentRunExecutor(repo, Dispatch())
    monkeypatch.setattr(executor, "_release_matches", lambda *_a: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_a: True)

    done = executor._execute_step(
        "run-v4", "owner-v4", "claim-v4", snapshot, plan, step,
        Blackboard(), session_id="session-v4",
    )

    assert done is False, repo.failed
    assert len(repo.started) == len(repo.finished) == 1 and not repo.failed
    result = repo.finished[0][1]["result"]
    assert result["capability_pipeline"] == "education_screening_structured_v4"
    assert result["education_screening_v4"]["schema_version"] == 4
    assert result["model_usage"]["status"] == "observed"
    assert "reply_candidate_unverified" not in result
