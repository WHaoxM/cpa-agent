from __future__ import annotations

import pytest

from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.prompt_selector import (
    PromptSelectionError,
    PromptSelectionKey,
    get_prompt_selector,
)


@pytest.mark.parametrize(
    "key,expected_ids",
    [
        (
            PromptSelectionKey("supervisor", InteractionMode.ASK, "text"),
            ["agent.ask.manual", "agent.ask.system"],
        ),
        (
            PromptSelectionKey("supervisor", InteractionMode.AGENT, "router"),
            ["agent.router.system"],
        ),
        (
            PromptSelectionKey("supervisor", InteractionMode.EXPERTS, "lead"),
            ["agent.experts.lead.system"],
        ),
        (
            PromptSelectionKey("subagent", InteractionMode.AGENT, "text", "match"),
            ["agent.manual", "agent.match.system", "agent.task_packet.system"],
        ),
    ],
)
def test_prompt_selector_matrix_routes(key, expected_ids):
    variables = {
        "router_context": "ctx",
        "message": "帮我做人岗匹配",
        "templates": "end_to_end",
        "roles_json": '["profile","match","qa"]',
        "skill_cards_json": "[]",
        "selected_capability_json": "null",
        "slots": "resume",
        "known_slots_json": "[]",
        "template_hint": "none",
        "card": "",
        "summary": "",
        "memory_digest": "",
    }
    bundle = get_prompt_selector().select(key, variables=variables)
    assert bundle.prompt_ids == expected_ids
    assert bundle.system.strip()
    assert bundle.cache_key


def test_ask_prompt_distinguishes_verified_facts_from_general_knowledge():
    bundle = get_prompt_selector().select(
        PromptSelectionKey("supervisor", InteractionMode.ASK, "text"),
    )
    assert "只有当前用户明确提供的信息，或本轮实际读取并能指出来源的内容，才标为已核实事实" in bundle.system
    assert "不得捏造具体数字、时间阈值、统计频率、研究结论、来源或个人偏好" in bundle.system
    assert "没有依据时避免“任何/所有/必然/一定”等绝对适用性表述" in bundle.system
    assert "可选示例或假设" in bundle.system


def test_ask_attachment_route_never_loads_internal_qa_prompt():
    bundle = get_prompt_selector().select(
        PromptSelectionKey("subagent", InteractionMode.ASK, "attachment", "qa")
    )
    assert "agent.qa.system" not in bundle.prompt_ids
    text = bundle.system
    assert "graph_query" not in text
    assert "db_query" not in text
    assert "blackboard" not in text.lower()
    assert "TaskPacket" not in text


def test_prompt_selector_missing_route_fail_fast():
    with pytest.raises(PromptSelectionError, match="missing runtime prompt route"):
        get_prompt_selector().select(
            PromptSelectionKey("supervisor", InteractionMode.ASK, "not_a_kind")
        )


def test_agent_dynamic_planner_and_finalizer_have_registered_content_fingerprints():
    selector = get_prompt_selector()
    lead = selector.select(
        PromptSelectionKey("supervisor", InteractionMode.AGENT, "lead"),
        variables={
            "templates": "end_to_end",
            "roles_json": '["qa","profile"]',
            "skill_cards_json": "[]",
            "selected_capability_json": "null",
            "slots": "resume",
            "known_slots_json": "[]",
            "template_hint": "none",
            "card": "",
            "summary": "",
            "memory_digest": "",
            "message": "帮我解释院校背景",
        },
    )
    final = selector.select(
        PromptSelectionKey("supervisor", InteractionMode.AGENT, "final"),
        variables={"user_message": "帮我解释院校背景", "steps_json": "[]"},
    )
    assert "school-context" in lead.system
    assert lead.metadata["prompt_sha256"]
    assert final.metadata["prompt_sha256"]
