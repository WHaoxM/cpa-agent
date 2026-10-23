from app.services.agent_runtime.experts_lead import (
    _infer_known_slots,
    _normalize,
    experts_lead_decide,
)
from app.services.agent_runtime.plan import (
    _evaluate_step_success,
    _is_substantive_payload,
    build_plan_packets,
    detect_plan_template,
    run_plan,
    validate_plan_steps,
)
from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.model_router import route_model
from app.services.agent_runtime.task_packet import build_standard_packet


def test_template_matcher_is_hint_only():
    assert detect_plan_template("从简历到报告") is None
    assert detect_plan_template("专家模式干什么用的") is None


def test_lead_meta_forces_explain():
    out = _normalize(
        {
            "user_job": "meta",
            "action": "run_plan",
            "template": "end_to_end",
            "goal_summary": "用户在问专家模式干什么用",
            "vagueness": "clear",
            "missing_slots": [],
            "reply": "",
            "confidence": 0.9,
        },
        "专家模式干什么用的",
        plan_mode=True,
        hint=None,
        known_slots=[],
        blackboard=Blackboard(),
    )
    assert out["action"] == "explain"
    assert out["template"] is None
    assert out["goal_summary"]
    assert "Experts" in out["reply"] or "多专家" in out["reply"]


def test_lead_run_plan_blocked_without_resume():
    out = _normalize(
        {
            "user_job": "execute",
            "action": "run_plan",
            "template": "end_to_end",
            "goal_summary": "想生成完整职业报告",
            "vagueness": "partial",
            "missing_slots": ["resume"],
            "reply": "",
            "confidence": 0.8,
            "plan_sketch": ["1.画像", "2.匹配", "3.报告"],
            "handoff_brief": "生成职业报告",
        },
        "帮我出一份职业报告",
        plan_mode=True,
        hint="end_to_end",
        known_slots=[],
        blackboard=Blackboard(),
    )
    assert out["action"] == "clarify"
    assert "resume" in out["missing_slots"]
    assert out["clarify_options"]


def test_lead_run_plan_with_resume_ok():
    out = _normalize(
        {
            "user_job": "execute",
            "action": "run_plan",
            "template": "end_to_end",
            "goal_summary": "基于已有背景跑完整链路",
            "vagueness": "clear",
            "missing_slots": [],
            "reply": "",
            "confidence": 0.85,
            "handoff_brief": "根据用户会计背景生成画像、匹配与报告",
        },
        "根据我的背景生成职业报告",
        plan_mode=True,
        hint="end_to_end",
        known_slots=["resume"],
        blackboard=Blackboard(),
    )
    assert out["action"] == "run_plan"
    assert out["template"] == "end_to_end"
    assert out["handoff_brief"]
    assert out["plan_sketch"]


def test_template_only_legacy_plan_produces_valid_step_goals():
    out = _normalize(
        {
            "user_job": "execute",
            "action": "run_plan",
            "template": "end_to_end",
            "goal_summary": "基于已确认背景生成职业方案",
            "handoff_brief": "目标岗位为杭州数据分析，计划六周",
            "vagueness": "clear",
            "missing_slots": [],
        },
        "根据我的背景生成职业方案",
        plan_mode=False,
        hint=None,
        known_slots=["resume"],
        blackboard=Blackboard(),
    )
    assert out["action"] == "run_plan"
    packets = build_plan_packets(
        out["steps"],
        "根据我的背景生成职业方案",
        handoff_brief=out["handoff_brief"],
        goal_summary=out["goal_summary"],
    )
    assert [packet.subagent for packet in packets] == ["profile", "match", "report"]
    assert all("目标岗位为杭州数据分析" in packet.goal for packet in packets)


def test_infer_known_slots_from_blackboard():
    bb = Blackboard({"attachments.mounted": {"files": [{"file_id": "a"}]}})
    assert "resume" in _infer_known_slots("随便", bb)


def test_handoff_brief_in_first_packet():
    packets = build_plan_packets(
        "end_to_end",
        "跑一下",
        handoff_brief="为会计专业学生生成端到端职业方案",
        goal_summary="端到端报告",
    )
    assert "会计专业" in packets[0].goal


def test_dynamic_plan_accepts_single_step_and_pins_allowlisted_skill():
    specs = validate_plan_steps([{
        "role": "qa",
        "goal": "只读解释本人已确认的院校背景",
        "skill_ids": ["school-context"],
    }])
    packets = build_plan_packets(specs, "解释我的院校背景")
    assert len(packets) == 1
    assert packets[0].subagent == "qa"
    assert packets[0].skill_ids == ["school-context"]


def test_dynamic_plan_allows_at_most_three_distinct_roles():
    specs = [
        {"role": role, "goal": f"第 {i} 步", "skill_ids": []}
        for i, role in enumerate(["profile", "match", "report", "qa"])
    ]
    try:
        validate_plan_steps(specs)
    except ValueError as exc:
        assert "distinct roles" in str(exc)
    else:
        raise AssertionError("four distinct roles must be rejected")


def test_dynamic_plan_rejects_role_skill_and_execution_control_tampering():
    invalid = [
        [{"role": "root", "goal": "x", "skill_ids": []}],
        [{"role": "profile", "goal": "x", "skill_ids": ["school-context"]}],
        [{"role": "qa", "goal": "x", "skill_ids": [], "tools": ["pipeline_run"]}],
    ]
    for plan in invalid:
        try:
            validate_plan_steps(plan)
        except ValueError:
            continue
        raise AssertionError("invalid role, skill, or tool control must fail closed")


def test_lead_normalizes_dynamic_steps_and_rejects_invalid_roles_without_execution():
    valid = _normalize(
        {
            "user_job": "execute",
            "action": "run_plan",
            "template": None,
            "steps": [{
                "role": "qa",
                "goal": "只读解释已确认的学校背景",
                "skill_ids": ["school-context"],
            }],
            "goal_summary": "解释院校背景",
            "vagueness": "clear",
            "missing_slots": [],
            "handoff_brief": "根据本人已确认资料解释",
        },
        "解释我的学校背景",
        plan_mode=False,
        hint=None,
        known_slots=[],
        blackboard=Blackboard(),
    )
    assert valid["action"] == "run_plan"
    assert valid["steps"][0]["skill_ids"] == ["school-context"]

    invalid = _normalize(
        {
            "user_job": "execute",
            "action": "run_plan",
            "steps": [{"role": "admin", "goal": "改 D1", "skill_ids": []}],
            "vagueness": "clear",
        },
        "帮我改 D1",
        plan_mode=False,
        hint=None,
        known_slots=["resume"],
        blackboard=Blackboard(),
    )
    assert invalid["action"] == "clarify"
    assert invalid["steps"] == []


def test_dynamic_qa_plan_ignores_unrelated_model_missing_slots():
    out = _normalize(
        {
            "user_job": "execute",
            "action": "run_plan",
            "steps": [{
                "role": "qa",
                "goal": "只读解释当前文本中的院校标签与资料缺口",
                "skill_ids": ["school-context"],
            }],
            "goal_summary": "只读解释院校标签",
            "vagueness": "clear",
            "missing_slots": ["resume", "portrait", "match", "target_role"],
            "handoff_brief": "只依据当前提供的文本，不修改业务资料",
        },
        "只读解释院校标签",
        plan_mode=False,
        hint=None,
        known_slots=[],
        blackboard=Blackboard(),
    )
    assert out["action"] == "run_plan"
    assert out["missing_slots"] == []
    assert out["steps"][0]["role"] == "qa"


def test_dynamic_profile_plan_retains_server_required_resume_clarification():
    out = _normalize(
        {
            "user_job": "execute",
            "action": "run_plan",
            "steps": [{
                "role": "profile",
                "goal": "根据简历生成职业画像",
                "skill_ids": [],
            }],
            "goal_summary": "生成职业画像",
            "vagueness": "clear",
            "missing_slots": [],
            "handoff_brief": "根据用户简历生成职业画像",
        },
        "生成职业画像",
        plan_mode=False,
        hint=None,
        known_slots=[],
        blackboard=Blackboard(),
    )
    assert out["action"] == "clarify"
    assert out["missing_slots"] == ["resume"]


def test_lead_classify_route_uses_usage_verified_qwen_plus_default():
    route = route_model("lead_classify")
    assert route == {
        "model": "qwen-plus",
        "temperature": 0.1,
        "max_tokens": 700,
    }


def test_hollow_present_true_not_substantive():
    assert _is_substantive_payload({"present": True}, had_tools=False) is False
    assert _is_substantive_payload(
        {"resource_id": "p1", "source_task_id": "t1", "status": "completed", "verified": True},
        had_tools=True,
    )


def test_hollow_step_not_ok():
    bb = Blackboard()
    packet = build_standard_packet("profile", goal="画像", user_utterance="x")
    ok, err = _evaluate_step_success(
        packet,
        bb,
        committed={"portrait.current": {"present": True}},
        out={"reply": "ok", "tool_calls": []},
    )
    assert ok is False
    assert err


def test_run_plan_stops_on_hollow():
    bb = Blackboard()
    packets = [build_standard_packet("profile", goal="画像", user_utterance="做画像")]

    def fake_invoke(packet, blackboard):
        return {
            "reply": "无法生成，缺少数据",
            "tool_calls": [],
            "blackboard_updates": {},
        }

    results, narrative = run_plan(packets, bb, fake_invoke)
    assert results[0].ok is False
    assert "未达成" in narrative or "未写入" in narrative


def test_experts_lead_fallback_explain_without_llm(monkeypatch):
    import app.services.agent_runtime.experts_lead as lead

    monkeypatch.setattr(lead, "_llm_lead", lambda *a, **k: None)
    d = experts_lead_decide("专家模式干什么用的", plan_mode=True)
    assert d["action"] == "clarify"
    assert d["source"] == "lead_unavailable"
    assert d.get("template") is None
    assert d.get("goal_summary") == ""
    assert d["confidence"] == 0


def test_selected_capability_is_explicitly_presented_to_lead_and_fit_is_returned(monkeypatch):
    import json
    from app.services.agent_runtime import model_router
    from app.services.agent_runtime import experts_lead as lead
    import app.utils.llm_client as llm_client

    selected = {
        "expert_id": "career-coach", "capability_id": "career-planning",
        "version": "1.0.0", "name": "职业规划顾问", "role": "qa",
        "skill_ids": ["school-context"], "max_steps": 6, "read_only": True,
    }
    seen = {}

    class FakeLLM:
        def chat(self, *, messages, **_kwargs):
            seen["prompt"] = messages[0]["content"]
            return json.dumps({
                "user_job": "execute", "action": "run_plan",
                "capability_fit": "supported", "vagueness": "clear",
                "goal_summary": "解释已确认院校背景与求职准备",
                "steps": [{
                    "role": "qa", "goal": "只读解释院校背景并给出求职建议",
                    "skill_ids": ["school-context"],
                }],
            }, ensure_ascii=False)

    monkeypatch.setattr(model_router, "route_model", lambda _route: {
        "temperature": 0.0, "max_tokens": 256, "model": "test-model",
    })
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: FakeLLM())
    result = lead.experts_lead_decide(
        "请解释已确认的学校背景与校招准备",
        plan_mode=True, interaction_mode="experts",
        selected_capability=selected,
    )

    assert '"expert_id":"career-coach"' in seen["prompt"]
    assert '"capability_id":"career-planning"' in seen["prompt"]
    assert "不得改选、忽略" in seen["prompt"]
    assert result["action"] == "run_plan"
    assert result["capability_fit"] == "supported"
    assert result["steps"][0]["skill_ids"] == ["school-context"]


def test_selected_capability_lead_can_mark_request_unsupported_for_switch():
    selected = {"expert_id": "career-coach", "capability_id": "career-planning"}
    out = _normalize(
        {
            "user_job": "execute", "action": "run_plan",
            "capability_fit": "unsupported", "vagueness": "clear",
            "goal_summary": "要求生成正式岗位报告",
            "steps": [{
                "role": "qa", "goal": "生成正式岗位报告",
                "skill_ids": ["school-context"],
            }],
        },
        "生成正式岗位报告",
        plan_mode=True,
        hint=None,
        known_slots=[],
        blackboard=Blackboard(),
        selected_capability=selected,
    )
    assert out["action"] == "clarify"
    assert out["capability_fit"] == "unsupported"
