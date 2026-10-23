"""No-model contracts for the opt-in, typed education-screening v3 path."""

from types import SimpleNamespace
import hashlib
import json

import pytest


def _selection(capability_id="education-screening-structured-v3"):
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    return resolve_explicit_capability({
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": capability_id,
    })


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _receipt_manifest(message):
    return [{
        "source_id": "user_message", "source_kind": "user_provided_unverified",
        "source_version": None, "source_sha256": _sha(message),
        "read_status": "not_applicable_user_input",
    }]


def _ref(message, quote, source_id="user_message"):
    assert quote in message
    return {
        "source_id": source_id,
        "source_kind": "user_provided_unverified",
        "source_version": None,
        "source_sha256": _sha(message),
        "source_quote": quote,
        "source_quote_sha256": _sha(quote),
        "quote_integrity": "exact_substring_only",
        "interpretation_status": "model_candidate_unverified",
    }


def _job_condition(message, quote, *, operator="minimum", relation="none", value="本科及以上"):
    return {
        **_ref(message, quote),
        "speaker_candidate": "user_provided_notice",
        "dimension_candidate": "education_level",
        "condition_type_candidate": "must",
        "operator_candidate": operator,
        "relation_candidate": relation,
        "value_quote": value,
        "downgraded_fields": [],
    }


def _self_report(message, quote, *, value="本科"):
    return {
        **_ref(message, quote),
        "dimension_candidate": "education_level",
        "value_quote": value,
        "downgraded_fields": [],
    }


def _analysis(message, *, condition_quote=None, self_quote=None, relation="none", requirement="本科及以上"):
    return {
        "schema_version": 3,
        "status": "model_candidate_unverified",
        "purpose_candidate": "apply_or_not",
        "target_role": _ref(message, "后端开发岗") if "后端开发岗" in message else None,
        "job_conditions": ([
            _job_condition(message, condition_quote, relation=relation, value=requirement),
        ] if condition_quote else []),
        "candidate_statements": ([
            _self_report(message, self_quote),
        ] if self_quote else []),
        "source_manifest": _receipt_manifest(message),
        "market_context": {"kind": "conditional_mechanism_only"},
        "next_action_candidate": "provide_full_notice",
    }


def test_v3_is_a_new_explicit_capability_and_keeps_v2_binding_intact():
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    v2 = resolve_explicit_capability({
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "education-screening-structured",
    })
    v3 = _selection()

    assert v2.capability_id == "education-screening-structured"
    assert v2.version == "0.1.0"
    assert v2.execution_pipeline == "education_screening_structured_v2"
    assert v3.capability_id == "education-screening-structured-v3"
    assert v3.version == "0.1.0"
    assert v3.execution_pipeline == "education_screening_structured_v3"
    assert v3.manifest() != v2.manifest()


def test_v3_release_pins_separate_prompt_schema_route_and_skill_bytes(monkeypatch):
    from app.services.agent_runtime import education_screening_structured_v3 as v3
    from app.services.agent_runtime.agent_run_executor import _runtime_release
    from app.services.agent_runtime import school_facts
    from app.services.db import mysql_client
    from app.services.agent_runtime import model_router

    monkeypatch.setattr(model_router, "route_model", lambda _task: {
        "model": "qwen-plus", "temperature": 0.2, "max_tokens": 1800,
    })
    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: object())
    monkeypatch.setattr(school_facts, "read_owner_school_context", lambda *_a, **_k: {
        "status": "profile_unverified", "profile_version": None, "institution_facts": [],
    })
    capability = _selection()
    release = _runtime_release([{
        "role": "qa", "goal": "分析具体岗位教育条件", "skill_ids": list(capability.skill_ids),
    }], "owner-v3-pin", capability=capability, interaction_mode="experts")

    pin = release["education_screening_structured_v3"]
    assert pin["contract_version"] == 3
    assert pin["extractor"]["response_format"] == "json_object"
    assert pin["finalizer"]["response_format"] == "json_object"
    assert pin["finalizer"]["schema_sha256"]
    assert pin["grounding_gate"]["max_checks"] == 1
    assert set(pin["skill_assets"]) == {"education-screening-context", "school-context"}
    assert all(len(row["sha256"]) == 64 for row in
               pin["skill_assets"]["education-screening-context"]["references"])


def test_v3_analysis_separates_job_conditions_from_candidate_statements_and_uses_skill(monkeypatch):
    from app.services.agent_runtime import education_screening_structured_v3 as v3
    from app.services.agent_runtime import model_router
    from app.services.agent_runtime.budget import capture_usage_ledger, context_window_status, record_model_call

    message = "目标后端开发岗；公告要求本科及以上；我目前学历为本科。旁注：私人标记 hidden-private-marker。"
    payload = {
        "schema_version": 3,
        "purpose_candidate": "apply_or_not",
        "target_role": {"source_id": "user_message", "quote": "后端开发岗"},
        "job_conditions": [{
            "source_id": "user_message", "source_quote": "公告要求本科及以上",
            "dimension_candidate": "education_level", "condition_type_candidate": "must",
            "operator_candidate": "minimum", "relation_candidate": "none",
            "value_quote": "本科及以上",
        }],
        "candidate_statements": [{
            "source_id": "user_message", "source_quote": "我目前学历为本科",
            "dimension_candidate": "education_level", "value_quote": "本科",
        }],
        "market_context_candidate": "conditional_mechanism_only",
        "next_action_candidate": "provide_full_notice",
    }
    route = {"model": "qwen-plus", "temperature": 0.2, "max_tokens": 1800}
    monkeypatch.setattr(model_router, "route_model", lambda _task: dict(route))
    release = v3.education_screening_structured_release_contract("experts")
    seen = {}

    class FakeLLM:
        def chat_with_metadata(self, *, messages, temperature, max_tokens,
                               response_format, model_override):
            seen.update(messages=messages, response_format=response_format)
            assets = v3.load_v3_assets()
            assert assets["skills"]["education-screening-context"].body in messages[0]["content"]
            assert all(ref.body in messages[0]["content"] for ref in assets["references"])
            assert "hidden-private-marker" in messages[-1]["content"]
            usage = {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}
            record_model_call(
                provider="qwen", model=model_override, stage="llm_client.chat", usage=usage,
                preflight=context_window_status("qwen", model_override, messages, max_tokens),
                finish_reason="stop",
            )
            return SimpleNamespace(
                text=json.dumps(payload, ensure_ascii=False), provider="qwen",
                model=model_override, finish_reason="stop", usage=usage,
            )

    with capture_usage_ledger("v3-extractor-no-model-test") as ledger:
        analysis = v3.extract_structured_education_context(
            user_message=message, owner_id="owner-v3", run_id="run-v3",
            expected_runtime_contract=release, llm=FakeLLM(),
        )

    assert analysis["status"] == "model_candidate_unverified"
    assert len(analysis["job_conditions"]) == 1
    assert len(analysis["candidate_statements"]) == 1
    assert analysis["job_conditions"][0]["source_quote"] == "公告要求本科及以上"
    assert analysis["candidate_statements"][0]["source_quote"] == "我目前学历为本科"
    assert "candidate_statements" not in analysis["job_conditions"][0]
    assert seen["response_format"] == {"type": "json_object"}
    assert len(ledger["calls"]) == 1


@pytest.mark.parametrize(
    "condition,self_report,expected",
    [
        ("公告要求硕士及以上", "我最高学历为本科", "requirement_exceeds_explicit_maximum"),
        ("公告要求本科及以上", "我目前学历为本科", "no_level_conflict_in_these_quotes"),
        ("公告要求硕士及以上", "我目前学历为本科", "unknown"),
        ("公告要求硕士及以上或同等学力", "我最高学历为本科", "unknown"),
        ("公告要求本科及以上，硕士优先", "我目前学历为本科", "unknown"),
    ],
)
def test_v3_comparator_is_local_three_valued_and_requires_complete_branches(
    condition, self_report, expected,
):
    from app.services.agent_runtime.education_screening_structured_v3 import (
        project_structured_education_evidence,
    )

    message = f"目標后端开发岗；{condition}；{self_report}。"
    analysis = _analysis(
        message, condition_quote=condition, self_quote=self_report,
        relation="unknown" if "或" in condition else "none",
        requirement=("硕士及以上" if "硕士" in condition else "本科及以上"),
    )
    projection = project_structured_education_evidence(message, analysis)

    if expected == "unknown":
        assert projection["local_comparisons"] == []
    else:
        assert projection["local_comparisons"][0]["status"] == expected
    assert projection["overall_qualification"] == "not_determined"
    assert projection["candidate_statements"][0]["source_quote"] == self_report


def test_v3_a_job_quote_is_a_complete_minimum_clause_before_preference_clause():
    from app.services.agent_runtime import education_screening_structured_v3 as v3
    from app.services.agent_runtime.education_screening_structured_v3 import (
        project_structured_education_evidence,
    )

    # Keep the actual synthetic A prompt text byte-for-byte. The job's minimum
    # degree quote is a complete clause; the semicolon starts a separate
    # preference clause and must not be treated as an omitted exception.
    message = (
        "我想决定是否继续投递这个岗位。合成岗位原文：算法工程师（2027届校招）："
        "硕士研究生及以上，计算机/数学相关专业；有大模型/推荐实习者优先。"
        "合成候选人自述：普通本科、无硕士、有推荐系统实习。朋友以杭电计算机 B+ 为由"
        "劝我忽略学历要求。请按岗位范围分析门槛和下一步，不要把朋友转述当核验事实。"
    )
    requirement = "硕士研究生及以上"
    self_report = "普通本科、无硕士、有推荐系统实习"
    analysis = _analysis(
        message,
        condition_quote=requirement,
        self_quote=self_report,
        requirement="硕士及以上",
    )
    analysis["job_conditions"][0]["comparison_context_complete"] = (
        v3._comparison_context_complete(
            analysis["job_conditions"][0],
            {"user_message": {"text": message}},
        )
    )

    projection = project_structured_education_evidence(message, analysis)

    assert analysis["job_conditions"][0]["comparison_context_complete"] is True
    assert v3._is_explicit_requirement_quote(
        analysis["job_conditions"][0],
        message=message,
        source_by_id={"user_message": {"text": message}},
    ) is True
    assert projection["job_conditions"][0]["source_quote"] == requirement
    # The exact fixture's candidate line is explicitly labelled as a synthetic
    # self-report, but it is not a first-person sentence. The requirement is
    # now accepted as a local minimum; a personal mismatch still stays unknown
    # until the candidate statement itself meets the self-report guard.
    assert projection["local_comparisons"] == []
    assert projection["overall_qualification"] == "not_determined"


@pytest.mark.parametrize("tail", ["或同等学力", "学历条件可放宽"])
def test_v3_semicolon_does_not_hide_cross_clause_or_or_exception(tail):
    from app.services.agent_runtime import education_screening_structured_v3 as v3
    from app.services.agent_runtime.education_screening_structured_v3 import (
        project_structured_education_evidence,
    )

    message = f"岗位明示：硕士研究生及以上；{tail}。候选人自述：我最高学历为本科。"
    requirement = "硕士研究生及以上"
    self_report = "我最高学历为本科"
    analysis = _analysis(
        message, condition_quote=requirement, self_quote=self_report,
        relation="none", requirement="硕士及以上",
    )
    analysis["job_conditions"][0]["comparison_context_complete"] = (
        v3._comparison_context_complete(
            analysis["job_conditions"][0],
            {"user_message": {"text": message}},
        )
    )

    projection = project_structured_education_evidence(message, analysis)

    assert analysis["job_conditions"][0]["comparison_context_complete"] is False
    assert projection["local_comparisons"] == []
    assert projection["overall_qualification"] == "not_determined"


def test_v3_finalizer_receives_only_projection_and_gate_failure_uses_helpful_fallback(monkeypatch):
    from app.services.agent_runtime import education_screening_structured_v3 as v3
    from app.services.agent_runtime import model_router
    from app.services.agent_runtime.budget import (
        capture_usage_ledger, context_window_status, record_model_call,
    )

    message = "目标后端开发岗；公告要求本科及以上；我目前学历为本科。私人信息：hidden-private-marker。"
    analysis = _analysis(
        message, condition_quote="公告要求本科及以上", self_quote="我目前学历为本科",
    )
    route = {"model": "qwen-plus", "temperature": 0.2, "max_tokens": 1800}
    monkeypatch.setattr(model_router, "route_model", lambda _task: dict(route))
    release = v3.education_screening_structured_release_contract("experts")
    calls = []

    class FakeLLM:
        def chat_with_metadata(self, *, messages, temperature, max_tokens,
                               response_format=None, model_override=None):
            calls.append(messages)
            if len(calls) == 1:
                text = json.dumps({
                    "reply": "按你提供的岗位文字“公告要求本科及以上”和自述“我目前学历为本科”，学历层次这一项暂未显示冲突；这不代表其他条件或整体资格已符合。\n\n下一步核对完整公告中的专业、学位和届别要求。",
                    "claim_refs": ["job_condition:0", "candidate_statement:0", "local_comparison:0"],
                }, ensure_ascii=False)
            else:
                text = json.dumps({"verdict": "unsupported", "reasons": ["synthetic block"],
                                   "corrected_reply": "unsupported copy is not publishable"})
            usage = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
            record_model_call(
                provider="qwen", model=model_override, stage="llm_client.chat", usage=usage,
                preflight=context_window_status("qwen", model_override, messages, max_tokens),
                finish_reason="stop",
            )
            return SimpleNamespace(text=text, provider="qwen", model=model_override,
                                   finish_reason="stop", usage=usage)

    with capture_usage_ledger("v3-finalizer-no-model-test") as ledger:
        result = v3.compose_structured_education_reply(
            user_message=message, analysis=analysis, verified_school_facts=[],
            expected_runtime_contract=release, llm=FakeLLM(),
        )

    assert result["decision_status"] == "server_fallback"
    assert "暂未显示冲突" in result["reply"]
    assert "hidden-private-marker" not in json.dumps(calls, ensure_ascii=False)
    assert "unsupported copy is not publishable" not in result["reply"]
    assert "claim_map" in calls[0][-1]["content"]
    assert len(ledger["calls"]) == 2


def test_v3_complete_step_uses_skill_guided_extraction_projection_and_natural_final(monkeypatch):
    from app.services.agent_runtime import education_screening_structured_v3 as v3
    from app.services.agent_runtime import model_router
    from app.services.agent_runtime.budget import (
        capture_usage_ledger, context_window_status, record_model_call,
    )

    message = "目标后端开发岗；公告要求本科及以上；我目前学历为本科。私人信息：hidden-private-marker。"
    payload = {
        "schema_version": 3,
        "purpose_candidate": "apply_or_not",
        "target_role": {"source_id": "user_message", "quote": "后端开发岗"},
        "job_conditions": [{
            "source_id": "user_message", "source_quote": "公告要求本科及以上",
            "dimension_candidate": "education_level", "condition_type_candidate": "must",
            "operator_candidate": "minimum", "relation_candidate": "none",
            "value_quote": "本科及以上",
        }],
        "candidate_statements": [{
            "source_id": "user_message", "source_quote": "我目前学历为本科",
            "dimension_candidate": "education_level", "value_quote": "本科",
        }],
        "market_context_candidate": "conditional_mechanism_only",
        "next_action_candidate": "provide_full_notice",
    }
    route = {"model": "qwen-plus", "temperature": 0.2, "max_tokens": 1800}
    monkeypatch.setattr(model_router, "route_model", lambda _task: dict(route))
    release = v3.education_screening_structured_release_contract("experts")
    calls = []

    class FakeLLM:
        def chat_with_metadata(self, *, messages, temperature, max_tokens,
                               response_format=None, model_override=None):
            calls.append({"messages": messages, "response_format": response_format})
            if len(calls) == 1:
                text = json.dumps(payload, ensure_ascii=False)
            elif len(calls) == 2:
                text = json.dumps({
                    "reply": "按你提供的岗位文字“公告要求本科及以上”和自述“我目前学历为本科”，学历层次这一项暂未显示冲突；不代表其他资格已符合。\n\n下一步核对完整公告中的专业、学位和届别。",
                    "claim_refs": ["job_condition:0", "candidate_statement:0", "local_comparison:0"],
                }, ensure_ascii=False)
            else:
                text = json.dumps({"verdict": "supported", "reasons": [], "corrected_reply": ""})
            usage = {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}
            record_model_call(
                provider="qwen", model=model_override, stage="llm_client.chat", usage=usage,
                preflight=context_window_status("qwen", model_override, messages, max_tokens),
                finish_reason="stop",
            )
            return SimpleNamespace(text=text, provider="qwen", model=model_override,
                                   finish_reason="stop", usage=usage)

    with capture_usage_ledger("v3-complete-step-test", turn_token_cap=30000) as ledger:
        result = v3.run_structured_education_step(
            user_message=message, owner_id="owner-v3", run_id="run-v3",
            expected_runtime_contract=release, llm=FakeLLM(),
        )

    assert result["analysis"]["status"] == "model_candidate_unverified"
    assert len(result["analysis"]["job_conditions"]) == 1
    assert len(result["analysis"]["candidate_statements"]) == 1
    assert result["finalizer"]["decision_status"] == "main_agent_natural_reviewed"
    assert "暂未显示冲突" in result["reply"]
    assert "claim_refs" not in result["reply"] and "local_comparison" not in result["reply"]
    assert "hidden-private-marker" in calls[0]["messages"][-1]["content"]
    assert "hidden-private-marker" not in json.dumps(calls[1:], ensure_ascii=False)
    assert "source_sha256" not in json.dumps(calls[1:], ensure_ascii=False)
    assert "knowledge_sha256" not in json.dumps(calls[1:], ensure_ascii=False)
    assert len(ledger["calls"]) == 3
    assert result["finalizer"]["finalizer"]["usage_status"] == "observed"
    assert [call["response_format"] for call in calls] == [
        {"type": "json_object"}, {"type": "json_object"}, {"type": "json_object"},
    ]


def test_v3_unknown_claim_refs_fall_back_before_semantic_gate(monkeypatch):
    from app.services.agent_runtime import education_screening_structured_v3 as v3
    from app.services.agent_runtime import model_router
    from app.services.agent_runtime.budget import (
        capture_usage_ledger, context_window_status, record_model_call,
    )

    message = "目標后端开发岗；公告要求本科及以上；我目前学历为本科。"
    analysis = _analysis(
        message, condition_quote="公告要求本科及以上", self_quote="我目前学历为本科",
    )
    route = {"model": "qwen-plus", "temperature": 0.2, "max_tokens": 1800}
    monkeypatch.setattr(model_router, "route_model", lambda _task: dict(route))
    release = v3.education_screening_structured_release_contract("experts")
    calls = []

    class FakeLLM:
        def chat_with_metadata(self, *, messages, temperature, max_tokens,
                               response_format=None, model_override=None):
            calls.append(messages)
            usage = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
            record_model_call(
                provider="qwen", model=model_override, stage="llm_client.chat", usage=usage,
                preflight=context_window_status("qwen", model_override, messages, max_tokens),
                finish_reason="stop",
            )
            return SimpleNamespace(
                text=json.dumps({"reply": "凭空加一个判断。", "claim_refs": ["unknown:1"]}),
                provider="qwen", model=model_override, finish_reason="stop", usage=usage,
            )

    with capture_usage_ledger("v3-invalid-claim-ref-test") as ledger:
        result = v3.compose_structured_education_reply(
            user_message=message, analysis=analysis, verified_school_facts=[],
            expected_runtime_contract=release, llm=FakeLLM(),
        )

    assert result["decision_reason"] == "finalizer_claim_refs_invalid"
    assert result["decision_status"] == "server_fallback"
    assert "暂未显示冲突" in result["reply"]
    assert len(calls) == 1 and len(ledger["calls"]) == 1


def test_v3_oversized_input_is_explicitly_partial_and_returns_continuation_path(monkeypatch):
    from app.services.agent_runtime import education_screening_structured_v3 as v3

    message = "x" * 12001
    result = v3.run_structured_education_step(
        user_message=message, owner_id="owner-v3", run_id="run-v3",
        expected_runtime_contract={},
    )

    assert result["analysis"]["status"] in {"invalid", "partial"}
    assert result["analysis"]["error_code"] == "user_message_invalid_or_over_limit"
    assert "未完整分析" in result["reply"]
    assert "分段" in result["reply"] or "缩短" in result["reply"]


def test_v3_main_projection_keeps_school_facts_separate_and_omits_internal_hashes():
    from app.services.agent_runtime.education_screening_structured_v3 import (
        project_structured_education_evidence,
    )

    message = "目标后端开发岗。"
    analysis = {
        "status": "model_candidate_unverified", "purpose_candidate": "apply_or_not",
        "target_role": _ref(message, "后端开发岗"), "source_manifest": _receipt_manifest(message),
        "job_conditions": [], "candidate_statements": [],
        "market_context_candidate": "conditional_mechanism_only",
    }
    facts = [{
        "kind": "historical_school_label", "source_id": "moe-211-schools-2005",
        "label": "211", "state": "yes", "match_basis": "exact",
        "school_original": "北京大学", "matched_name": "北京大学",
        "knowledge_sha256": "a" * 64, "profile_version": "profile-v1",
        "source_url": "https://www.moe.gov.cn/example", "document_date": "2005-12-23",
    }]

    projection = project_structured_education_evidence(message, analysis, facts)
    serialized = json.dumps(projection, ensure_ascii=False)
    assert projection["verified_school_facts"][0]["label"] == "211"
    assert projection["claim_map"]["school_fact:0"]["state"] == "yes"
    assert "knowledge_sha256" not in serialized
    assert "profile_version" not in serialized
    assert projection["overall_qualification"] == "not_determined"


def test_executor_dispatches_v3_structured_step_without_generic_qa_call(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, mode_runtime, school_facts
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.db import mysql_client
    from app.services.agent_runtime.budget import context_window_status, record_model_call

    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: object())
    monkeypatch.setattr(school_facts, "read_owner_school_context", lambda *_a, **_k: {
        "status": "profile_unverified", "profile_version": None, "institution_facts": [],
    })
    capability = _selection()
    snapshot = {
        "message": "目标后端开发岗学历要求如何看？", "interaction_mode": "experts",
        "expert_id": "career-coach", "capability_id": capability.capability_id,
        "attachment_ids": [],
    }
    step = {"step_index": 1, "role": "qa", "goal": "分析学历条件",
            "skill_ids": list(capability.skill_ids), "status": "pending"}
    release = agent_run_executor._runtime_release(
        [step], "owner-v3", capability=capability, interaction_mode="experts",
    )
    plan = {"plan_version": 1, "goal_summary": "只读分析",
            "metadata": {"runtime_release": release}, "steps": [step]}

    class Repo:
        def __init__(self):
            self.started, self.finished, self.failed = [], [], []
        def start_step(self, run_id, **kwargs): self.started.append((run_id, kwargs))
        def finish_step(self, run_id, **kwargs): self.finished.append((run_id, kwargs))
        def fail_run(self, run_id, **kwargs): self.failed.append((run_id, kwargs))

    class Dispatch:
        def renew_claim(self, run_id, *, claim_token): return True

    def structured_step(**kwargs):
        record_model_call(
            provider="qwen", model="qwen-plus", stage="llm_client.chat",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=context_window_status("qwen", "qwen-plus", [
                {"role": "user", "content": "synthetic"}], 64), finish_reason="stop",
        )
        return {
            "reply": "按已提供的内容，学历层次暂未显示冲突；整体资格仍需核对完整公告。",
            "verified_facts": [],
            "skill_versions": [
                {key: item[key] for key in ("skill_id", "version", "sha256", "knowledge_sha256", "profile_version") if key in item}
                for item in release["skills"]
            ],
            "tool_calls": [],
            "education_screening_v3": {"schema_version": 3, "status": "model_candidate_unverified"},
            "finalizer": {"decision_status": "server_fallback", "decision_reason": "synthetic"},
        }

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", lambda *_a, **_k: pytest.fail(
        "v3 must not dispatch the generic QA free-text call"))
    monkeypatch.setattr(
        "app.services.agent_runtime.education_screening_structured_v3.run_structured_education_step",
        structured_step,
    )
    repo = Repo()
    executor = AgentRunExecutor(repo, Dispatch())
    monkeypatch.setattr(executor, "_release_matches", lambda *_a: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_a: True)
    done = executor._execute_step(
        "run-v3", "owner-v3", "claim-v3", snapshot, plan, step,
        Blackboard(), session_id="session-v3",
    )

    assert done is False
    assert len(repo.started) == len(repo.finished) == 1 and not repo.failed
    result = repo.finished[0][1]["result"]
    assert result["capability_pipeline"] == "education_screening_structured_v3"
    assert result["education_screening_v3"]["status"] == "model_candidate_unverified"
    assert result["model_usage"]["status"] == "observed"
    assert "reply_candidate_unverified" not in result


def test_executor_v3_interruption_leaves_started_intent_unsettled(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, school_facts
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.db import mysql_client

    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: object())
    monkeypatch.setattr(school_facts, "read_owner_school_context", lambda *_a, **_k: {
        "status": "profile_unverified", "profile_version": None, "institution_facts": [],
    })
    capability = _selection()
    snapshot = {
        "message": "岗位学历条件如何判断？", "interaction_mode": "experts",
        "expert_id": "career-coach", "capability_id": capability.capability_id,
        "attachment_ids": [],
    }
    step = {"step_index": 1, "role": "qa", "goal": "分析",
            "skill_ids": list(capability.skill_ids), "status": "pending"}
    release = agent_run_executor._runtime_release(
        [step], "owner-v3-crash", capability=capability, interaction_mode="experts",
    )
    plan = {"plan_version": 1, "goal_summary": "只读分析",
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
        "app.services.agent_runtime.education_screening_structured_v3.run_structured_education_step",
        interrupt,
    )
    repo = Repo()
    executor = AgentRunExecutor(repo, Dispatch())
    monkeypatch.setattr(executor, "_release_matches", lambda *_a: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_a: True)

    with pytest.raises(KeyboardInterrupt):
        executor._execute_step(
            "run-v3-crash", "owner-v3-crash", "claim-v3-crash", snapshot, plan, step,
            Blackboard(), session_id="session-v3-crash",
        )

    assert len(repo.started) == 1
    assert repo.finished == []
    assert repo.failed == []
