from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage


def _snapshot():
    from app.services.agent_runtime.turn_contract import normalize_message_parts

    card = normalize_message_parts([{
        "type": "data_card",
        "schema_version": 1,
        "title": "岗位比较",
        "card_type": "comparison",
        "source_refs": [{"source_id": "fixture:jobs:v1", "label": "合成岗位样例", "version": "v1"}],
        "items": [
            {"label": "第二项", "value": "数据平台工程师", "entity_id": "ent_job_second", "entity_version": "job:v2"},
            {"label": "第三项", "value": "后端开发工程师", "entity_id": "ent_job_third", "entity_version": "job:v3"},
        ],
    }])[0]
    state = SimpleNamespace(
        student_id="owner-1",
        messages=[{
            "message_id": "msg_card_1",
            "role": "assistant",
            "content": "岗位卡片已生成。",
            "metadata": {"parts_v1": [card]},
        }],
    )
    return card, state


def _make_gated_tool(monkeypatch, *, bound_owner="owner-1", session_id="session-1", state=None):
    from app.services.agent_runtime.gated_tools import build_gated_tools, make_gate_context
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime import tool_factory
    import app.services.agent_runtime.gated_tools as gated_tools

    monkeypatch.setattr(tool_factory, "_ALL_TOOLS", {})
    monkeypatch.setattr(gated_tools, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(
        "app.services.session_manager.get_session_manager",
        lambda: SimpleNamespace(get_session=lambda sid: state if sid == session_id else None),
    )
    gate = make_gate_context(
        interaction_mode=InteractionMode.ASK,
        subagent="qa",
        session_id=session_id,
        student_id=bound_owner,
        plan_tool_allowlist={"component_snapshot_read"},
        allow_artifact_commit=False,
    )
    tools = build_gated_tools("qa", gate, [])
    assert [tool.name for tool in tools] == ["component_snapshot_read"]
    return tools[0]


def test_ask_component_tool_reads_exact_historical_component_and_entity(monkeypatch):
    card, state = _snapshot()
    tool = _make_gated_tool(monkeypatch, state=state)

    output = json.loads(tool.invoke({
        "component_id": card["component_id"],
        "schema_version": 1,
        "entity_id": "ent_job_third",
        "entity_version": "job:v3",
    }))

    assert output["status"] == "completed"
    assert output["snapshot_kind"] == "historical_read_only"
    assert output["message_id"] == "msg_card_1"
    assert output["entity"]["value"] == "后端开发工程师"
    assert output["entity"]["entity_version"] == "job:v3"
    assert output["source_refs"][0]["source_id"] == "fixture:jobs:v1"


def test_ask_component_tool_is_bound_to_owner_and_rejects_stale_identity(monkeypatch):
    card, state = _snapshot()
    foreign_bound_tool = _make_gated_tool(monkeypatch, bound_owner="owner-2", state=state)
    unavailable = json.loads(foreign_bound_tool.invoke({
        "component_id": card["component_id"], "schema_version": 1,
    }))
    assert unavailable == {"status": "unavailable", "reason": "session_not_available"}

    bound_tool = _make_gated_tool(monkeypatch, bound_owner="owner-1", state=state)
    stale = json.loads(bound_tool.invoke({
        "component_id": card["component_id"],
        "schema_version": 1,
        "entity_id": "ent_job_third",
        "entity_version": "job:v2",
    }))
    assert stale["status"] == "stale"
    assert stale["reason"] == "entity_version_mismatch"


def test_ask_component_tool_refuses_unknown_schema_and_extra_scope_args(monkeypatch):
    card, state = _snapshot()
    tool = _make_gated_tool(monkeypatch, state=state)
    unsupported = json.loads(tool.invoke({
        "component_id": card["component_id"], "schema_version": 2,
    }))
    assert unsupported["reason"] == "unsupported_schema_version"

    with pytest.raises(Exception):
        tool.invoke({
            "component_id": card["component_id"],
            "schema_version": 1,
            "session_id": "another-session",
        })


def test_component_lookup_capability_requires_server_projected_component_id():
    from app.services.agent_runtime.mode_runtime import _ask_component_lookup_available

    assert not _ask_component_lookup_available(None)
    assert not _ask_component_lookup_available(SimpleNamespace(
        system_tail="<SERVER_COMPONENT_SNAPSHOTS>{\"cards\":[]}</SERVER_COMPONENT_SNAPSHOTS>",
        messages=[],
    ))
    assert _ask_component_lookup_available(SimpleNamespace(
        system_tail=(
            '<SERVER_COMPONENT_SNAPSHOTS>{"cards":[{"component_id":"cmp_'
            + "a" * 24
            + '"}]}</SERVER_COMPONENT_SNAPSHOTS>'
        ),
        messages=[],
    ))


def test_ask_scoped_loop_can_retrieve_snapshot_after_context_projection(monkeypatch):
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.task_packet import build_standard_packet
    from app.services.agent_runtime import tool_factory
    import app.services.agent_runtime.gated_tools as gated_tools
    import app.services.langgraph_agent as langgraph_agent

    card, state = _snapshot()
    monkeypatch.setattr(tool_factory, "_ALL_TOOLS", {})
    monkeypatch.setattr(gated_tools, "enqueue_audit", lambda **_kwargs: None)
    monkeypatch.setattr(
        "app.services.session_manager.get_session_manager",
        lambda: SimpleNamespace(get_session=lambda sid: state if sid == "session-1" else None),
    )

    class FakeBoundModel:
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "component_snapshot_read",
                    "args": {
                        "component_id": card["component_id"],
                        "schema_version": 1,
                        "entity_id": "ent_job_third",
                        "entity_version": "job:v3",
                    },
                    "id": "call-component-read",
                    "type": "tool_call",
                }])
            tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
            assert tool_messages
            assert "后端开发工程师" in str(tool_messages[-1].content)
            return AIMessage(content="卡片第二项是数据平台工程师，第三项是后端开发工程师。")

    fake_model = FakeBoundModel()
    monkeypatch.setattr(
        langgraph_agent,
        "_build_llm_with_tools",
        lambda _tools, _temperature, _max_tokens: fake_model,
    )
    packet = build_standard_packet(
        subagent="qa",
        goal="比较历史卡片的第二项和第三项",
        user_utterance="比较历史卡片的第二项和第三项",
        write_keys=[],
    )
    out = scoped_invoke(
        packet,
        Blackboard(),
        allow_tools=True,
        interaction_mode=InteractionMode.ASK,
        session_id="session-1",
        student_id="owner-1",
        plan_tool_allowlist={"component_snapshot_read"},
        allow_artifact_commit=False,
        ask_context_messages=[{"role": "user", "content": "比较历史卡片的第二项和第三项"}],
        ask_context_appendix=(
            '<SERVER_COMPONENT_SNAPSHOTS>{"cards":[{"component_id":"'
            + card["component_id"]
            + '","schema_version":1}]}</SERVER_COMPONENT_SNAPSHOTS>'
        ),
    )

    assert fake_model.calls == 2
    assert out["reply"] == "卡片第二项是数据平台工程师，第三项是后端开发工程师。"
    assert len(out["tool_calls"]) == 1
    assert out["tool_calls"][0]["status"] == "done"


def test_ask_mode_runtime_grants_component_read_only_for_projected_card(monkeypatch):
    import time
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    captured = []

    class FakeLLM:
        def chat(self, **_kwargs):
            return "卡片中第二项和第三项分别是两个岗位。"

    def fake_scoped_invoke(packet, _blackboard, **kwargs):
        captured.append((packet, kwargs))
        return {
            "reply": "卡片中第二项和第三项分别是两个岗位。",
            "tool_calls": [],
            "incomplete": False,
        }

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    monkeypatch.setattr("app.services.agent_runtime.scoped_invoke.scoped_invoke", fake_scoped_invoke)
    component_id = "cmp_" + "b" * 24
    question = "比较这张卡片的第二项和第三项"
    assembled = SimpleNamespace(
        system_prompt="服务端 Ask 上下文",
        system_tail=(
            '<SERVER_COMPONENT_SNAPSHOTS>{"cards":[{"component_id":"'
            + component_id
            + '","schema_version":1}]}</SERVER_COMPONENT_SNAPSHOTS>'
        ),
        messages=[{"role": "user", "content": question}],
        truncated_slots=["component_context"],
    )

    result = ModeRuntime()._run_ask(
        question, Blackboard(), "服务端 Ask 上下文", [], time.time(), "session-1",
        None, "", student_id="owner-1", turn_file_ids=[], mount_errors=[],
        assembled_context=assembled,
        evidence_pack={
            "status": "empty", "items": [], "sources": [],
            "coverage": {"reason": "evidence_not_needed"},
        },
    )

    assert len(captured) == 1
    packet, options = captured[0]
    assert packet.write_keys == []
    assert options["allow_tools"] is True
    assert options["plan_tool_allowlist"] == {"component_snapshot_read", "component_snapshot_discover"}
    assert options["student_id"] == "owner-1"
    assert options["session_id"] == "session-1"
    assert result["reply"] == "卡片中第二项和第三项分别是两个岗位。"


def test_component_discovery_recovers_snapshots_when_context_omitted_component_id(monkeypatch):
    from app.services.tools.component_snapshot_discover import create_component_snapshot_discover_tool

    card, _state = _snapshot()

    class BoundManager:
        def get_owner_scoped_message_page(self, session_id, student_id, **kwargs):
            assert (session_id, student_id) == ("session-1", "owner-1")
            assert kwargs["include_components"] is True
            return {
                "status": "completed",
                "total_messages": 1,
                "messages": [{
                    "message_id": "msg_card_1",
                    "role": "assistant",
                    "content": "岗位卡片已生成。",
                    "parts_v1": [card],
                }],
            }

    monkeypatch.setattr("app.services.session_manager.get_session_manager", lambda: BoundManager())
    tool = create_component_snapshot_discover_tool(student_id="owner-1", session_id="session-1")
    result = json.loads(tool.invoke({"query": "岗位比较", "offset": 0, "limit": 5}))
    assert result["status"] == "completed"
    assert result["cards"][0]["component_id"] == card["component_id"]
    assert result["cards"][0]["component_ordinal"] == 1
    assert result["cards"][0]["items"][1]["item_ordinal"] == 2
    assert result["cards"][0]["items"][1]["entity_id"] == "ent_job_third"
    assert result["cards"][0]["items"][1]["entity_version"] == "job:v3"
