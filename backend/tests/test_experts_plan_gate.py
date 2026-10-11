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
)
from app.services.agent_runtime.blackboard import Blackboard
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
