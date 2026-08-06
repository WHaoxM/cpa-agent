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
