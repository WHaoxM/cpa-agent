from app.services.agent_runtime.fast_path import visible_user_text
from app.services.agent_runtime.modes import InteractionMode, budget_for


def test_ask_budget_disables_ltm_by_default():
    assert budget_for(InteractionMode.ASK).allow_memory_read is False
    assert budget_for(InteractionMode.AGENT).allow_memory_read is True
    assert budget_for(InteractionMode.EXPERTS).allow_memory_read is True


def test_visible_user_text_only_normalizes_legacy_protocol():
    raw = "[用户概况] 预测方向：机器学习工程师；已掌握技能：Python | 询问：你能帮我做什么"
    assert visible_user_text(raw) == "你能帮我做什么"
    assert visible_user_text("帮我看看简历优劣势") == "帮我看看简历优劣势"
