"""Opt-in education-screening capability contracts (kept off the legacy path)."""

from types import SimpleNamespace

import pytest


def _request_capability(capability_id):
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    return resolve_explicit_capability({
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": capability_id,
    })


def _candidate(source_quote, *, speaker, operator="unknown", relation="none",
               condition_type="unclear", dimension="education_level", value_quote=""):
    return {
        "source_id": "user_message",
        "source_kind": "user_provided_unverified",
        "source_version": None,
        "source_sha256": "a" * 64,
        "source_quote": source_quote,
        "quote_integrity": "exact_substring_only",
        "interpretation_status": "model_candidate_unverified",
        "speaker_candidate": speaker,
        "dimension_candidate": dimension,
        "condition_type_candidate": condition_type,
        "operator_candidate": operator,
        "relation_candidate": relation,
        "value_quote": value_quote,
        "qualification_use": "not_permitted",
        "downgraded_fields": [],
        "diagnostics": [],
    }


def _current_pipeline_release():
    from app.services.agent_runtime.education_screening import (
        education_screening_release_contract,
    )

    return education_screening_release_contract("experts")


def _mock_pipeline_release():
    import hashlib
    import json

    from app.services.agent_runtime.education_screening import (
        EducationAnswerDecision,
        _EXTRACTOR_SYSTEM_PROMPT,
        education_answer_decision_instruction,
        _schema_sha256,
    )

    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    schema_digest = hashlib.sha256(json.dumps(
        EducationAnswerDecision.model_json_schema(), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    route = {"model": "test-model", "temperature": 0.2, "max_tokens": 1200}
    return {
        "contract_version": 1,
        "interaction_mode": "experts",
        "renderer_contract_version": 1,
        "extractor": {
            "prompt_sha256": digest(_EXTRACTOR_SYSTEM_PROMPT),
            "schema_sha256": _schema_sha256(),
            "route_task": "ask_answer", "route": route,
            "temperature": 0.0, "max_tokens": 1200,
        },
        "finalizer": {
            "prompt_bundle_sha256": "b" * 64,
            "instruction_sha256": digest(education_answer_decision_instruction()),
            "schema_sha256": schema_digest,
            "route_task": "ask_answer", "route": route,
            "temperature": 0.2, "max_tokens": 900,
        },
    }


def test_education_capability_is_explicit_opt_in_and_legacy_manifest_is_unchanged():
    old = _request_capability("career-planning")
    new = _request_capability("education-screening")

    assert old.manifest() == {
        "expert_id": "career-coach",
        "capability_id": "career-planning",
        "version": "1.0.0",
        "name": "职业规划顾问",
        "role": "qa",
        "skill_ids": ["school-context"],
        "max_steps": 6,
        "read_only": True,
    }
    assert new.manifest()["capability_id"] == "education-screening"
    assert new.manifest()["version"] == "0.1.0"
    assert new.manifest()["max_steps"] == 1
    assert new.manifest()["skill_ids"] == [
        "education-screening-context", "school-context",
    ]
    new.validate_plan([{
        "role": "qa", "goal": "按岗位教育条件和背景语境分析",
        "skill_ids": ["education-screening-context", "school-context"],
    }])
    with pytest.raises(Exception):
        new.validate_plan([{
            "role": "qa", "goal": "遗漏能力包 Skill",
            "skill_ids": ["school-context"],
        }])
    with pytest.raises(Exception):
        _request_capability("career-planning-evidence")


def test_candidate_skill_is_visible_and_loadable_only_under_new_capability():
    from app.services.agent_runtime.skill_registry import (
        SkillLoadError, allowed_skill_ids, load_skill, skill_cards,
    )

    legacy_cards = skill_cards(role="qa")
    assert [item["id"] for item in legacy_cards] == ["school-context"]
    assert allowed_skill_ids("qa") == frozenset({"school-context"})
    with pytest.raises(SkillLoadError):
        load_skill("education-screening-context", role="qa")

    cards = skill_cards(role="qa", capability_id="education-screening")
    assert {item["id"] for item in cards} == {
        "school-context", "education-screening-context",
    }
    assert "recruitment notice" in next(
        item["description"] for item in cards
        if item["id"] == "education-screening-context"
    )
    loaded = load_skill(
        "education-screening-context", role="qa",
        capability_id="education-screening",
    )
    assert loaded.version == "0.3.0"
    assert len(loaded.sha256) == 64
    assert "推理顺序" in loaded.body


def test_capability_scope_flows_into_task_packet_and_plan_validation():
    from app.services.agent_runtime.plan import build_plan_packets, validate_plan_steps
    from app.services.agent_runtime.task_packet import build_standard_packet

    steps = [{
        "role": "qa", "goal": "对照用户提供的教育条件",
        "skill_ids": ["education-screening-context", "school-context"],
    }]
    normalized = validate_plan_steps(steps, capability_id="education-screening")
    packet = build_plan_packets(
        normalized, "按公告判断学历要求", capability_id="education-screening",
    )[0]
    assert packet.capability_id == "education-screening"
    assert packet.skill_ids == steps[0]["skill_ids"]
    packet.validate()

    with pytest.raises(Exception):
        validate_plan_steps(steps)
    with pytest.raises(Exception):
        build_standard_packet(
            "qa", "不应隐式装载候选 Skill",
            skill_ids=["education-screening-context"],
        )


def test_new_runtime_release_pins_candidate_skill_without_changing_legacy_base(monkeypatch):
    from app.services.agent_runtime import agent_run_executor, school_facts
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability
    from app.services.agent_runtime.skill_registry import _SKILLS
    from app.services.db import mysql_client

    cap = _request_capability("education-screening")
    old_cap = _request_capability("career-planning")
    monkeypatch.setattr(agent_run_executor, "_base_runtime_release", lambda: {
        "executor_contract": 1, "skill_cards": [{"id": "school-context"}],
    })
    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: object())
    monkeypatch.setattr(school_facts, "_load_source", lambda: ({}, "official-data-hash"))
    monkeypatch.setattr(school_facts, "read_owner_school_context", lambda *_a, **_kw: {
        "profile_version": "confirmed-resume-test", "status": "unavailable",
        "institution_facts": [],
    })
    default_cards = agent_run_executor._base_runtime_release()["skill_cards"]
    assert [row["id"] for row in default_cards] == ["school-context"]

    # Runtime release must resolve the actually selected capability-scoped Skill.
    release = agent_run_executor._runtime_release(
        [{"role": "qa", "goal": "提取学历条件", "skill_ids": [
            "education-screening-context", "school-context",
        ]}],
        "owner_for_release_pin_test",
        capability=cap,
    )
    assert {row["skill_id"] for row in release["skills"]} == {
        "education-screening-context", "school-context",
    }
    assert release["capability_binding"] == cap.manifest()
    assert release["release_sha256"]
    education_release = release["education_pipeline"]
    assert education_release["contract_version"] == 1
    assert set(education_release) == {
        "contract_version", "interaction_mode", "renderer_contract_version",
        "extractor", "finalizer",
    }
    assert len(education_release["extractor"]["prompt_sha256"]) == 64
    assert len(education_release["extractor"]["schema_sha256"]) == 64
    assert education_release["extractor"]["route"]["model"]
    assert len(education_release["finalizer"]["prompt_bundle_sha256"]) == 64
    assert len(education_release["finalizer"]["instruction_sha256"]) == 64
    assert len(education_release["finalizer"]["schema_sha256"]) == 64
    assert education_release["finalizer"]["route"] == education_release["extractor"]["route"]
    new_skill = next(row for row in release["skills"]
                     if row["skill_id"] == "education-screening-context")
    assert new_skill["version"] == "0.3.0"
    assert len(new_skill["sha256"]) == 64

    from app.services.agent_runtime import education_screening
    new_steps = [{"role": "qa", "goal": "提取学历条件", "skill_ids": [
        "education-screening-context", "school-context",
    ]}]
    original_prompt = education_screening._EXTRACTOR_SYSTEM_PROMPT
    monkeypatch.setattr(
        education_screening, "_EXTRACTOR_SYSTEM_PROMPT", original_prompt + "\n版本变化",
    )
    changed_prompt_release = agent_run_executor._runtime_release(
        new_steps, "owner_for_release_pin_test", capability=cap,
    )
    assert changed_prompt_release["education_pipeline"]["extractor"]["prompt_sha256"] != education_release["extractor"]["prompt_sha256"]
    assert changed_prompt_release["release_sha256"] != release["release_sha256"]
    executor = agent_run_executor.AgentRunExecutor(object(), object())
    assert not executor._release_matches(
        release, new_steps, "owner_for_release_pin_test", cap, "experts",
    )
    monkeypatch.setattr(education_screening, "_EXTRACTOR_SYSTEM_PROMPT", original_prompt)

    from app.services.agent_runtime import model_router
    original_route = model_router.route_model
    monkeypatch.setattr(model_router, "route_model", lambda task: {
        **original_route(task), "model": "hot-switched-model",
    })
    changed_route_release = agent_run_executor._runtime_release(
        new_steps, "owner_for_release_pin_test", capability=cap,
    )
    assert changed_route_release["education_pipeline"]["extractor"]["route"]["model"] == "hot-switched-model"
    assert changed_route_release["release_sha256"] != release["release_sha256"]
    assert not executor._release_matches(
        release, new_steps, "owner_for_release_pin_test", cap, "experts",
    )
    monkeypatch.setattr(model_router, "route_model", original_route)

    legacy_steps = [{"role": "qa", "goal": "只读职业建议",
                     "skill_ids": ["school-context"]}]
    legacy_key = ("education-screening-context",)
    candidate_spec = _SKILLS.pop(legacy_key[0])
    try:
        old_without_candidate = agent_run_executor._runtime_release(
            legacy_steps, "owner_for_release_pin_test", capability=old_cap,
        )
    finally:
        _SKILLS[legacy_key[0]] = candidate_spec
    old_with_candidate = agent_run_executor._runtime_release(
        legacy_steps, "owner_for_release_pin_test", capability=old_cap,
    )
    assert "education_pipeline" not in old_with_candidate
    assert old_without_candidate == old_with_candidate
    assert old_without_candidate["release_sha256"] == old_with_candidate["release_sha256"]


def test_selected_candidate_skill_is_actually_loaded_into_qa_prompt(monkeypatch):
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.skill_registry import load_skill
    from app.services.agent_runtime.task_packet import build_standard_packet
    from app.services.agent_runtime import school_facts

    seen = {}

    class FakeLLM:
        def chat(self, *, messages, **_kwargs):
            seen["system"] = messages[0]["content"]
            return "已按来源限制分析，只读回复。"

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(school_facts, "read_owner_school_context", lambda *_a, **_kw: {
        "status": "profile_unverified", "profile_version": None,
        "institution_facts": [],
        "other_labels": {"985": "not_evaluated", "double_first_class": "not_evaluated"},
        "scoring_effect": "none", "next_action": "confirm_resume",
    })
    monkeypatch.setattr(school_facts, "project_verified_school_facts", lambda _value: [])
    packet = build_standard_packet(
        "qa", "分析用户提供的教育条件并说明边界",
        user_utterance="公告要求硕士及以上。",
        skill_ids=["education-screening-context", "school-context"],
        capability_id="education-screening",
    )
    result = scoped_invoke(
        packet, Blackboard(), allow_tools=False,
        interaction_mode=InteractionMode.EXPERTS, student_id=None,
    )
    loaded = load_skill(
        "education-screening-context", role="qa",
        capability_id="education-screening",
    )
    assert f'<PROJECT_SKILL id="education-screening-context" version="0.3.0" sha256="{loaded.sha256}"' in seen["system"]
    assert "推理顺序" in seen["system"]
    assert "<PROJECT_SKILL id=\"school-context\"" in seen["system"]
    assert result["skill_versions"][0]["skill_id"] == "education-screening-context"
    assert result["skill_versions"][0]["sha256"] == loaded.sha256


def test_education_renderer_gives_local_hard_gate_conclusion_without_global_eligibility():
    from app.services.agent_runtime.education_screening import (
        render_education_screening_reply,
    )

    conditions = [
        _candidate(
            "硕士及以上", speaker="user_provided_notice", operator="minimum",
            condition_type="must", value_quote="硕士及以上",
        ),
        _candidate(
            "仅有本科学历", speaker="candidate_self_report",
            value_quote="本科学历",
        ),
    ]
    reply = render_education_screening_reply(
        user_message="公告要求硕士及以上；我仅有本科学历。",
        conditions=conditions,
        verified_facts=[],
    )
    assert "按你提供的原文" in reply
    assert "学历这一项" in reply and "暂不满足" in reply
    assert "整体资格" in reply and "例外" in reply
    assert "source_quote" not in reply and "model_candidate_unverified" not in reply
    assert "D1" not in reply and "评分" not in reply


def test_bare_bachelor_report_does_not_infer_absence_of_a_masters_degree():
    from app.services.agent_runtime.education_screening import (
        render_education_screening_reply,
    )

    conditions = [
        _candidate(
            "硕士及以上", speaker="user_provided_notice", operator="minimum",
            condition_type="must", value_quote="硕士及以上",
        ),
        _candidate("本科", speaker="candidate_self_report", value_quote="本科"),
    ]
    reply = render_education_screening_reply(
        user_message="公告要求硕士及以上；学历：本科。",
        conditions=conditions, verified_facts=[],
    )
    assert "暂不满足" not in reply
    assert "不能仅凭学校标签" in reply or "信息不足" in reply


def test_finalizer_rejects_freeform_numeric_or_ats_content_and_uses_safe_renderer(monkeypatch, offline_budget_estimator):
    from app.services.agent_runtime import education_screening
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.modes import InteractionMode
    release = _mock_pipeline_release()
    monkeypatch.setattr(
        education_screening, "education_screening_release_contract",
        lambda _mode: release,
    )

    class PromptSelector:
        def select(self, _key, *, variables):
            return SimpleNamespace(
                system=variables["steps_json"],
                metadata={"prompt_sha256": release["finalizer"]["prompt_bundle_sha256"]},
            )

    class MalformedLLM:
        def chat_with_metadata(self, **kwargs):
            from app.services.agent_runtime.budget import (
                context_window_status, record_model_call,
            )
            usage = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
            record_model_call(
                provider="openai", model="gpt-4o-mini", stage="llm_client.chat",
                usage=usage,
                preflight=context_window_status(
                    "openai", "gpt-4o-mini", kwargs["messages"], 128,
                ), finish_reason="stop",
            )
            return SimpleNamespace(
                text='{"schema_version":1,"comparison_pairs":[],"risk_context":"mechanism_only",'
                     '"next_step":"provide_full_notice","include_school_fact":false,'
                     '"reply":"录取概率为87%，该公司ATS会直接刷掉。"}',
                provider="openai", model="gpt-4o-mini", finish_reason="stop", usage=usage,
            )

    monkeypatch.setattr(mode_runtime, "get_prompt_selector", lambda: PromptSelector())
    monkeypatch.setattr(
        "app.services.agent_runtime.model_router.route_model",
        lambda _route: {"temperature": 0.2, "max_tokens": 1200, "model": "test-model"},
    )
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: MalformedLLM())
    from app.services.agent_runtime.budget import capture_usage_ledger

    with capture_usage_ledger("agent_run_step"):
        result = ModeRuntime()._finalize_plan_reply(
            message="普通双非投算法岗有没有机会？",
            steps=[{"role": "qa", "status": "completed", "verified_facts": [],
                    "education_conditions": {"status": "invalid", "conditions": []}}],
            interaction_mode=InteractionMode.EXPERTS,
            education_screening_constrained=True,
            return_details=True,
            education_pipeline_release=release,
        )
    assert result["decision_status"] == "server_fallback"
    assert result["decision_reason"] == "typed_schema_invalid"
    assert "87%" not in result["reply"] and "ATS" not in result["reply"]
    assert "机制假设" in result["reply"] or "机制性" in result["reply"]


def test_education_renderer_no_jd_is_realistic_conditional_and_actionable():
    from app.services.agent_runtime.education_screening import (
        render_education_screening_reply,
    )

    reply = render_education_screening_reply(
        user_message="普通双非投算法岗是不是没戏？",
        conditions=[], verified_facts=[],
    )
    assert "不能仅凭学校标签" in reply or "不能从学校标签" in reply
    assert "名额有限" in reply or "竞争" in reply
    assert "机制" in reply or "可能" in reply
    assert "公告" in reply or "岗位" in reply
    assert not any(token in reply for token in ("%", "ATS", "录取概率", "绝无可能"))


@pytest.mark.parametrize("job_kind", ["其他技术岗", "非技术岗", "公务员/国企岗位"])
def test_education_renderer_does_not_blanket_reject_other_job_families(job_kind):
    from app.services.agent_runtime.education_screening import (
        render_education_screening_reply,
    )

    reply = render_education_screening_reply(
        user_message=f"双非背景可以投{job_kind}吗？",
        conditions=[], verified_facts=[],
    )
    assert "整体资格" not in reply or "不能仅凭学校标签" in reply
    assert "都不适合" not in reply and "一概" not in reply
    assert "具体岗位" in reply or "岗位要求" in reply or "公告" in reply


def test_education_renderer_complex_or_exception_never_decides_overall_qualification():
    from app.services.agent_runtime.education_screening import (
        render_education_screening_reply,
    )

    quote = "本科或硕士；退役士兵可放宽届别"
    conditions = [
        _candidate(
            quote, speaker="user_provided_notice", operator="one_of", relation="or",
            condition_type="must", value_quote="本科或硕士",
        ),
        _candidate(
            quote, speaker="user_provided_notice", operator="except", relation="exception",
            condition_type="exception", dimension="graduation_cohort",
            value_quote="放宽届别",
        ),
    ]
    reply = render_education_screening_reply(
        user_message=f"公告：{quote}。我仅有本科学历。",
        conditions=conditions, verified_facts=[],
    )
    assert "整体资格" in reply
    assert any(word in reply for word in ("分支", "例外", "完整原文"))
    assert "符合" not in reply or "不能判断整体资格是否符合" in reply


def test_new_finalizer_returns_natural_server_projection_not_internal_schema(monkeypatch, offline_budget_estimator):
    from app.services.agent_runtime import education_screening
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.modes import InteractionMode

    release = _mock_pipeline_release()
    monkeypatch.setattr(
        education_screening, "education_screening_release_contract",
        lambda _mode: release,
    )
    seen = {}

    class PromptSelector:
        def select(self, _key, *, variables):
            seen["steps_json"] = variables["steps_json"]
            return SimpleNamespace(
                system="教育筛选 finalizer",
                metadata={"prompt_sha256": release["finalizer"]["prompt_bundle_sha256"]},
            )

    class StructuredLLM:
        def chat_with_metadata(self, **kwargs):
            from app.services.agent_runtime.budget import (
                context_window_status, record_model_call,
            )
            record_model_call(
                provider="openai", model="gpt-4o-mini", stage="llm_client.chat",
                usage={"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
                preflight=context_window_status(
                    "openai", "gpt-4o-mini", kwargs["messages"], 128,
                ), finish_reason="stop",
            )
            return SimpleNamespace(
                text='{"schema_version":1,"comparison_pairs":[{"requirement_index":0,"self_report_index":1}],"risk_context":"mechanism_only","next_step":"provide_full_notice","include_school_fact":false}',
                provider="openai", model="gpt-4o-mini", finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            )

    monkeypatch.setattr(mode_runtime, "get_prompt_selector", lambda: PromptSelector())
    monkeypatch.setattr(
        "app.services.agent_runtime.model_router.route_model",
        lambda _route: {"temperature": 0.2, "max_tokens": 1200, "model": "test-model"},
    )
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: StructuredLLM())
    from app.services.agent_runtime.budget import capture_usage_ledger

    candidates = [
        _candidate("硕士及以上", speaker="user_provided_notice", operator="minimum",
                   condition_type="must", value_quote="硕士及以上"),
        _candidate("仅有本科学历", speaker="candidate_self_report",
                   value_quote="本科学历"),
    ]
    with capture_usage_ledger("agent_run_step") as ledger:
        reply = ModeRuntime()._finalize_plan_reply(
            message="公告要求硕士及以上；我仅有本科学历。",
            steps=[{
                "role": "qa", "status": "completed", "verified_facts": [],
                "education_conditions": {"status": "model_candidate_unverified",
                                         "conditions": candidates},
                "reply_candidate_unverified": "这里有不可信QA自由文本",
            }],
            interaction_mode=InteractionMode.EXPERTS,
            education_screening_constrained=True,
            education_pipeline_release=release,
        )
    assert "暂不满足" in reply
    assert "只读" not in reply and "JSON" not in reply
    assert "这里有不可信QA自由文本" not in seen["steps_json"]
    assert "source_quote" not in reply and "comparison_pairs" not in reply
    assert len(ledger["calls"]) == 1


def test_education_finalizer_without_pinned_release_fails_closed_before_dispatch(monkeypatch):
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.modes import InteractionMode

    called = []

    class NeverLLM:
        def chat_with_metadata(self, **_kwargs):
            called.append("dispatched")
            raise AssertionError("a finalizer without a release pin must not dispatch")

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: NeverLLM())
    result = ModeRuntime()._finalize_plan_reply(
        message="普通双非投算法岗有没有机会？",
        steps=[{"role": "qa", "status": "completed", "verified_facts": [],
                "education_conditions": {"status": "invalid", "conditions": []}}],
        interaction_mode=InteractionMode.EXPERTS,
        education_screening_constrained=True,
        return_details=True,
    )
    assert result["decision_status"] == "server_fallback"
    assert result["decision_reason"] == "runtime_release_missing"
    assert called == []


def test_education_finalizer_stale_route_pin_fails_closed_before_dispatch(monkeypatch):
    from app.services.agent_runtime import education_screening
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.modes import InteractionMode

    pinned = _mock_pipeline_release()
    current = _mock_pipeline_release()
    current["finalizer"]["route"]["model"] = "hot-switched-model"
    monkeypatch.setattr(
        education_screening, "education_screening_release_contract",
        lambda _mode: current,
    )
    called = []

    class NeverLLM:
        def chat_with_metadata(self, **_kwargs):
            called.append("dispatched")
            raise AssertionError("a changed route must not dispatch")

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: NeverLLM())
    result = ModeRuntime()._finalize_plan_reply(
        message="普通双非投算法岗有没有机会？",
        steps=[{"role": "qa", "status": "completed", "verified_facts": [],
                "education_conditions": {"status": "invalid", "conditions": []}}],
        interaction_mode=InteractionMode.EXPERTS,
        education_screening_constrained=True,
        return_details=True,
        education_pipeline_release=pinned,
    )
    assert result["decision_status"] == "server_fallback"
    assert result["decision_reason"] == "runtime_release_changed"
    assert called == []


def test_executor_new_pipeline_keeps_qa_extract_and_synthesis_in_one_durable_step(monkeypatch, offline_budget_estimator):
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    from app.services.agent_runtime.budget import context_window_status, record_model_call
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    snapshot = {
        "message": "公告要求硕士及以上；我仅有本科学历。",
        "interaction_mode": "experts", "expert_id": "career-coach",
        "capability_id": "education-screening", "attachment_ids": [],
    }
    cap = resolve_explicit_capability(snapshot)
    education_release = _current_pipeline_release()
    step = {
        "step_index": 1, "role": "qa", "goal": "分析学历条款",
        "skill_ids": ["education-screening-context", "school-context"],
        "status": "pending",
    }
    plan = {
        "plan_version": 1, "goal_summary": "判断学历条件与行动",
        "metadata": {"runtime_release": {
            "release_sha256": "pinned",
            "skills": [
                {"skill_id": "education-screening-context", "version": "0.3.0", "sha256": "skill-a"},
                {"skill_id": "school-context", "version": "1.0.2", "sha256": "skill-b",
                 "knowledge_sha256": "", "profile_version": ""},
            ],
            "education_pipeline": education_release,
        }},
        "steps": [step],
    }
    repo = _FakeRunRepo()
    executor = AgentRunExecutor(repo, _FakeRunDispatch())
    calls = []

    def invoke(packet, _board, *_args, **kwargs):
        calls.append(("qa", packet.capability_id, list(packet.skill_ids), kwargs["student_id"]))
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="qa",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            preflight=context_window_status(
                "openai", "gpt-4o-mini", [{"role": "user", "content": "qa"}], 64,
            ), finish_reason="stop",
        )
        return {"reply": "不可信QA正文。", "verified_facts": [],
                "tool_calls": [], "skill_versions": [
                    {"skill_id": "education-screening-context", "version": "0.3.0", "sha256": "skill-a"},
                    {"skill_id": "school-context", "version": "1.0.2", "sha256": "skill-b",
                     "knowledge_sha256": "", "profile_version": ""},
                ]}

    def extract(**kwargs):
        calls.append(("extract", kwargs["user_message"], kwargs["owner_id"],
                      kwargs["run_id"], kwargs["expected_runtime_contract"]))
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="extractor",
            usage={"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            preflight=context_window_status(
                "openai", "gpt-4o-mini", [{"role": "user", "content": "extract"}], 64,
            ), finish_reason="stop",
        )
        return {"status": "model_candidate_unverified", "conditions": [
            _candidate("硕士及以上", speaker="user_provided_notice", operator="minimum",
                       condition_type="must", value_quote="硕士及以上"),
            _candidate("仅有本科学历", speaker="candidate_self_report",
                       value_quote="本科学历"),
        ], "source_manifest": [], "input_message_sha256": "m" * 64}

    def finalizer(_self, **kwargs):
        calls.append(("finalizer", kwargs["education_screening_constrained"],
                      kwargs["steps"], kwargs["education_pipeline_release"]))
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="main-finalizer",
            usage={"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            preflight=context_window_status(
                "openai", "gpt-4o-mini", [{"role": "user", "content": "final"}], 64,
            ), finish_reason="stop",
        )
        return {
            "reply": "按你提供的原文和自述，学历这一项暂不满足；请核对完整公告中的例外。",
            "decision_status": "structured_decision_rendered",
            "decision_reason": None,
            "provider": "openai", "model": "gpt-4o-mini",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            "usage_status": "observed",
        }

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    monkeypatch.setattr(
        "app.services.agent_runtime.education_screening.extract_education_conditions", extract,
    )
    monkeypatch.setattr(ModeRuntime, "_finalize_plan_reply", finalizer)
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)

    done = executor._execute_step(
        "run_" + "d" * 32, "owner-test", "e" * 32,
        snapshot, plan, step, Blackboard(), session_id="session-test",
    )
    assert done is False, repo.failed
    assert [item[0] for item in calls] == ["qa", "extract", "finalizer"]
    assert calls[0][1:3] == (
        "education-screening",
        ["education-screening-context", "school-context"],
    )
    assert calls[1][1:4] == (snapshot["message"], "owner-test", "run_" + "d" * 32)
    assert calls[1][4] == education_release
    assert calls[2][1] is True
    assert calls[2][3] == education_release
    result = repo.finished[0][1]["result"]
    assert result["capability_reply"].startswith("按你提供的原文")
    assert result["reply_candidate_unverified"] == "不可信QA正文。"
    assert result["education_conditions"]["status"] == "model_candidate_unverified"
    assert result["model_usage"]["status"] == "observed"
    assert result["model_usage"]["call_count"] == 3
    assert result["education_pipeline_release_sha256"]
    assert result["verified_updates"] == {}
    final = AgentRunExecutor(object(), object())._finalize(
        snapshot["message"], [{"role": "qa", "status": "completed", "result": result}],
        "experts", capability=cap,
    )
    assert final == result["capability_reply"]


class _FakeRunRepo:
    def __init__(self):
        self.started = []
        self.finished = []
        self.failed = []

    def start_step(self, run_id, **kwargs):
        self.started.append((run_id, kwargs))

    def finish_step(self, run_id, **kwargs):
        self.finished.append((run_id, kwargs))

    def fail_run(self, run_id, **kwargs):
        self.failed.append((run_id, kwargs))


class _FakeRunDispatch:
    def renew_claim(self, run_id, *, claim_token):
        return True


def test_crash_after_qa_dispatch_leaves_intent_unfinished_for_reconciliation(monkeypatch, offline_budget_estimator):
    from app.services.agent_runtime import mode_runtime
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    from app.services.agent_runtime.budget import context_window_status, record_model_call
    from app.services.agent_runtime.blackboard import Blackboard

    snapshot = {"message": "请求", "interaction_mode": "experts",
                "expert_id": "career-coach", "capability_id": "education-screening"}
    education_release = _current_pipeline_release()
    plan = {"plan_version": 1, "goal_summary": "只读",
            "metadata": {"runtime_release": {
                "education_pipeline": education_release,
                "skills": [
                    {"skill_id": "education-screening-context", "version": "0.3.0", "sha256": "skill-a"},
                    {"skill_id": "school-context", "version": "1.0.2", "sha256": "skill-b",
                     "knowledge_sha256": "", "profile_version": ""},
                ],
            }},
            "steps": [{"step_index": 1, "role": "qa", "goal": "只读",
                       "skill_ids": ["education-screening-context", "school-context"],
                       "status": "pending"}]}
    repo = _FakeRunRepo()

    def invoke(*_args, **_kwargs):
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="qa",
            usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            preflight=context_window_status(
                "openai", "gpt-4o-mini", [{"role": "user", "content": "x"}], 64,
            ), finish_reason="stop",
        )
        raise KeyboardInterrupt("synthetic worker process interruption after provider dispatch")

    monkeypatch.setattr(mode_runtime, "_invoke_subagent", invoke)
    executor = AgentRunExecutor(repo, _FakeRunDispatch())
    monkeypatch.setattr(executor, "_release_matches", lambda *_args: True)
    monkeypatch.setattr(executor, "_claim_and_account_current", lambda *_args: True)

    with pytest.raises(KeyboardInterrupt):
        executor._execute_step(
            "run_" + "f" * 32, "owner-test", "1" * 32,
            snapshot, plan, plan["steps"][0], Blackboard(), session_id="s",
        )
    assert len(repo.started) == 1
    assert repo.finished == []
    assert repo.failed == []
