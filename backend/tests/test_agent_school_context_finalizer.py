"""Final replies for the school-context capability stay inside source facts."""

from types import SimpleNamespace

from app.services.agent_runtime import mode_runtime
from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.mode_runtime import ModeRuntime


def _verified_school_fact():
    return {
        "kind": "historical_school_label",
        "school_original": "北京大学",
        "matched_name": "北京大学",
        "match_basis": "exact",
        "label": "211",
        "state": "yes",
        "source_id": "moe-211-schools-2005",
        "source_title": "“211工程”学校名单",
        "source_publisher": "中华人民共和国教育部",
        "source_url": "https://www.moe.gov.cn/example",
        "document_date": "2005-12-23",
        "knowledge_sha256": "a" * 64,
        "profile_version": "resume_confirmed_test",
        "profile_assurance": "user_confirmed_resume_not_enrollment_verified",
        "coverage": "complete_historical_name_list",
        "other_labels": {"985": "not_evaluated", "double_first_class": "not_evaluated"},
        "scoring_effect": "none",
    }


def test_school_finalizer_replaces_unverified_document_id_and_hiring_claim(monkeypatch, caplog):
    seen = {}

    class PromptSelector:
        def select(self, _key, *, variables):
            return SimpleNamespace(system="Finalizer. facts=" + variables["steps_json"])

    class FakeLLM:
        def chat(self, *, messages, **_kwargs):
            seen["prompt"] = messages[0]["content"]
            return (
                "北京大学属于历史211高校，依据教重办〔2005〕1号；"
                "校名会带来基础信任度。"
            )

    monkeypatch.setattr(mode_runtime, "get_prompt_selector", lambda: PromptSelector())
    monkeypatch.setattr(
        "app.services.agent_runtime.model_router.route_model",
        lambda _route: {"temperature": 0.2, "max_tokens": 2000, "model": "test-model"},
    )
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())

    reply = ModeRuntime()._finalize_plan_reply(
        message="请解释这所学校的院校背景和求职影响。",
        steps=[{"role": "qa", "status": "completed", "verified_facts": [_verified_school_fact()]}],
        interaction_mode=InteractionMode.EXPERTS,
        school_context_constrained=True,
    )

    assert "教重办" not in reply
    assert "信任度" not in reply
    assert "北京大学" in reply and "211" in reply and "2005" in reply
    assert "985" in reply and "双一流" in reply
    assert "D1" not in reply or "没有修改画像或评分" in reply
    assert "verified_facts" in seen["prompt"]
    assert "不得写成否定结论" in seen["prompt"]
    assert "reason=unverified_document_identifier" in caplog.text


def test_school_finalizer_keeps_unknown_state_and_uses_safe_fallback_on_model_failure(monkeypatch):
    class PromptSelector:
        def select(self, _key, *, variables):
            return SimpleNamespace(system=variables["steps_json"])

    class BrokenLLM:
        def chat(self, **_kwargs):
            raise RuntimeError("synthetic model outage")

    unknown = _verified_school_fact()
    unknown.update({"school_original": "合成未知院校", "matched_name": None,
                    "match_basis": "unknown", "state": "unknown"})
    monkeypatch.setattr(mode_runtime, "get_prompt_selector", lambda: PromptSelector())
    monkeypatch.setattr(
        "app.services.agent_runtime.model_router.route_model",
        lambda _route: {"temperature": 0.2, "max_tokens": 2000, "model": "test-model"},
    )
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: BrokenLLM())

    reply = ModeRuntime()._finalize_plan_reply(
        message="是否属于211？",
        steps=[{"role": "qa", "status": "completed", "verified_facts": [unknown]}],
        interaction_mode=InteractionMode.EXPERTS,
        school_context_constrained=True,
    )

    assert "未知" in reply
    assert "不是 211" not in reply
    assert "合成未知院校" in reply


def test_finalizer_rejects_a_document_title_not_present_in_verified_source_facts():
    reply = (
        "依据教育部《关于公布211工程学校名单的通知》，"
        "北京大学与2005年历史211名单精确匹配。"
    )
    assert mode_runtime._school_context_final_violation(
        reply, [_verified_school_fact()],
    ) == "unverified_school_source_title"


def test_finalizer_rejects_a_negative_claim_that_conflicts_with_exact_match_fact():
    assert mode_runtime._school_context_final_violation(
        "北京大学未命中2005年历史211名单。", [_verified_school_fact()],
    ) == "verified_school_match_misrepresented_as_negative"


def test_unknown_school_validator_requires_uncertainty_without_negative_or_positive_claims():
    unknown = _verified_school_fact()
    unknown.update({"school_original": "合成未知院校", "matched_name": None,
                    "match_basis": "unknown", "state": "unknown"})
    facts = [unknown]

    assert mode_runtime._school_context_final_violation(
        "合成未知院校的211身份无法确认，需核对校名和名单覆盖范围。", facts,
    ) is None
    assert mode_runtime._school_context_final_violation(
        "合成未知院校不是211，目前仍无法确认。", facts,
    ) == "unknown_school_misrepresented_as_negative"
    assert mode_runtime._school_context_final_violation(
        "合成未知院校是211，但资料状态未知。", facts,
    ) == "unknown_school_positive_claim"


def test_school_finalizer_skips_model_when_no_server_verified_facts(monkeypatch):
    class NeverCalledLLM:
        def chat(self, **_kwargs):
            raise AssertionError("no source facts must not reach the model finalizer")

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: NeverCalledLLM())
    reply = ModeRuntime()._finalize_plan_reply(
        message="某校是否是211？",
        steps=[{"role": "qa", "status": "completed", "verified_facts": []}],
        interaction_mode=InteractionMode.EXPERTS,
        school_context_constrained=True,
    )
    assert "无法判断院校标签" in reply


def test_explicit_school_capability_finalizer_does_not_receive_unverified_candidate(monkeypatch):
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor

    seen = {}

    def capture(_self, **kwargs):
        seen.update(kwargs)
        return "captured"

    monkeypatch.setattr(ModeRuntime, "_finalize_plan_reply", capture)
    result = AgentRunExecutor(object(), object())._finalize(
        "请解释学校背景。",
        [{
            "role": "qa",
            "status": "completed",
            "result": {
                "verified_facts": [_verified_school_fact()],
                "reply_candidate_unverified": "模型编造教重办〔2005〕1号",
                "source_reads": [{"tool_name": "school_context_source_read"}],
                "tool_calls": [{"raw_result": "untrusted"}],
                "skill_versions": [{"sha256": "skill-hash"}],
            },
        }],
        "experts",
        capability=SimpleNamespace(skill_ids=("school-context",), execution_pipeline=None),
    )

    assert result == "captured"
    assert seen["school_context_constrained"] is True
    assert seen["steps"] == [{
        "role": "qa", "status": "completed", "verified_facts": [_verified_school_fact()],
    }]

