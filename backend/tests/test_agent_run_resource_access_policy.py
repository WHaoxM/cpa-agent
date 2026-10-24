from app.services.agent_runtime.resource_access_policy import (
    explicitly_forbids_personal_reads,
    has_verified_read_tool_result,
    needs_personal_context_clarification,
    no_read_clarification,
    no_read_safe_goal,
    sanitize_no_read_reply,
    step_requires_resource_read,
    validate_no_read_plan,
)


def test_explicit_no_read_is_detected_but_permitted_read_is_not():
    assert explicitly_forbids_personal_reads(
        "为虚构学生制定学习计划；不联网，不读取资料，不执行操作。",
    )
    assert explicitly_forbids_personal_reads(
        "Don't read my profile or attached materials.",
    )
    assert explicitly_forbids_personal_reads(
        "制定通用建议。", ["本轮禁止访问绑定的个人档案。"],
    )
    assert not explicitly_forbids_personal_reads(
        "请读取我绑定的经历材料，并结合我的背景给出建议。",
    )


def test_no_read_with_personalized_request_clarifies_without_access():
    message = "不要读取我的简历；请结合我的经历推荐适合我的职业方向。"
    assert needs_personal_context_clarification(message)
    clarification = no_read_clarification(message)
    assert clarification
    assert "我不会访问绑定资料" in clarification


def test_general_no_read_request_does_not_force_clarification():
    message = "为虚构学生解释数据库隔离级别；不联网，不读取资料。"
    assert explicitly_forbids_personal_reads(message)
    assert not needs_personal_context_clarification(message)
    assert no_read_clarification(message) is None


def test_read_step_requires_evidence_but_model_prose_is_not_a_receipt():
    assert step_requires_resource_read("读取绑定的探索资料并形成待确认建议")
    assert not step_requires_resource_read("只依据用户原话提供一般知识解释")
    assert not has_verified_read_tool_result({
        "reply": "已经读取并分析了资料。", "tool_calls": [],
    })
    assert not has_verified_read_tool_result({
        "tool_calls": [{
            "tool_name": "career_data_read", "status": "done",
            "summary": "资料已读取",
        }],
    })
    assert not has_verified_read_tool_result({
        "tool_calls": [{
            "tool_name": "career_data_read", "status": "error", "channel": "ui",
        }],
    })
    assert has_verified_read_tool_result({
        "tool_calls": [{
            "tool_name": "career_data_read", "status": "done", "channel": "ui",
        }],
    })


def test_no_read_reply_drops_model_authored_verified_evidence_section():
    reply = (
        "计划正文。\n\n已核验证据：\n"
        "- 所有内容基于 SQL:2016 标准和公开文档。\n"
        "- 未使用联网信息。\n\n"
        "缺口说明（如实标注）：未提供目标数据库。"
    )
    sanitized = sanitize_no_read_reply(reply)
    assert "计划正文。" in sanitized
    assert "缺口说明（如实标注）：未提供目标数据库。" in sanitized
    assert "已核验证据" not in sanitized
    assert "SQL:2016" not in sanitized
    assert "来源说明：本轮未联网或读取外部资料" in sanitized
    assert "一般知识处理，未外部核验" in sanitized


def test_no_read_plan_rejects_personal_skill_or_mutated_read_goal():
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability

    capability = resolve_explicit_capability({
        "interaction_mode": "experts",
        "expert_id": "career-coach",
        "capability_id": "education-screening-structured-v5",
    })
    safe = [{
        "role": "qa", "goal": no_read_safe_goal(),
        "skill_ids": ["education-screening-context"],
    }]
    assert validate_no_read_plan(capability, safe)

    unsafe_skill = [{
        **safe[0], "skill_ids": ["education-screening-context", "school-context"],
    }]
    assert not validate_no_read_plan(capability, unsafe_skill)
    unsafe_goal = [{**safe[0], "goal": "读取学校背景并给出建议"}]
    assert not validate_no_read_plan(capability, unsafe_goal)
    assert not validate_no_read_plan(None, [
        {"role": "qa", "goal": no_read_safe_goal(), "skill_ids": []},
        {"role": "qa", "goal": "额外读取", "skill_ids": []},
    ])
