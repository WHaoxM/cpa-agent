from app.services.agent_runtime.fast_path import (
    greeting_reply,
    is_capability_probe,
    is_pure_greeting,
    qa_needs_tools,
    visible_user_text,
    wants_document_content,
)
from app.services.agent_runtime.modes import InteractionMode, budget_for


def test_ask_budget_disables_ltm_by_default():
    """Claude/Qoder: Ask isolation is a mode budget, not keyword sniffing."""
    assert budget_for(InteractionMode.ASK).allow_memory_read is False
    assert budget_for(InteractionMode.AGENT).allow_memory_read is True
    assert budget_for(InteractionMode.EXPERTS).allow_memory_read is True


def test_visible_user_text_strips_profile_prefix():
    raw = "[用户概况] 预测方向：机器学习工程师；已掌握技能：Python | 询问：你能帮我做什么"
    assert visible_user_text(raw) == "你能帮我做什么"


def test_capability_probe_accel_still_works():
    """Capability probe remains a TTFT accel path, not the isolation mechanism."""
    assert is_capability_probe("你能帮我做什么")
    reply = greeting_reply("你能帮我做什么")
    assert "机器学习" not in reply
    assert "Ask" in reply or "问答" in reply


def test_prefixed_capability_strips_before_accel():
    raw = "[用户概况] 关注方向：ML | 询问：你能帮我做什么"
    assert is_capability_probe(raw)
    assert greeting_reply(raw)
    assert is_pure_greeting("你好")


def test_wants_document_content_resume_full_body():
    assert wants_document_content("这篇简历的完整内容")
    assert wants_document_content("开始读简历")
    assert wants_document_content("请把全文发我")
    assert not wants_document_content("帮我看看简历优劣势")
    assert qa_needs_tools("这篇简历的完整内容")
