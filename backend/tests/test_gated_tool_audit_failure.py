"""Tool outcomes must remain truthful when best-effort audit enqueue fails."""

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.modes import InteractionMode
from app.services.agent_runtime.task_packet import TaskPacket


def test_audit_enqueue_failure_does_not_turn_executed_tool_into_error(monkeypatch):
    import app.services.agent_runtime.gated_tools as gated
    import app.services.agent_runtime.scoped_invoke as scoped

    executed = []

    def read_profile(query: str) -> str:
        executed.append(query)
        return json.dumps({"status": "completed", "success": True, "summary": "read"})

    base = StructuredTool.from_function(
        func=read_profile, name="career_data_read", description="read profile",
    )
    monkeypatch.setattr(gated, "pre_tool_use", lambda _name, args, _ctx: SimpleNamespace(
        allowed=True, args=args, rewritten=False,
        verdict=SimpleNamespace(reason="ok"),
    ))
    monkeypatch.setattr(
        gated, "enqueue_audit",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )
    monkeypatch.setattr(gated, "get_artifact_committer", lambda: SimpleNamespace(
        commit_candidates=lambda *_args, **_kwargs: [],
    ))
    monkeypatch.setattr(
        scoped, "build_gated_tools",
        lambda _subagent, ctx, collector: [gated._wrap_one(base, ctx, collector)],
    )

    class FakeToolModel:
        def __init__(self):
            self.invocations = 0

        def invoke(self, _messages):
            self.invocations += 1
            if self.invocations == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "career_data_read",
                    "args": {"query": "synthetic"},
                    "id": "call-audit-fails",
                }])
            return AIMessage(content="Synthetic profile read completed.")

    model = FakeToolModel()
    monkeypatch.setattr(
        "app.services.langgraph_agent._build_llm_with_tools",
        lambda *_args: model,
    )

    result = scoped.scoped_invoke(
        TaskPacket(goal="Read profile", subagent="qa", read_keys=[], write_keys=[]),
        Blackboard(),
        interaction_mode=InteractionMode.AGENT,
    )

    assert executed == ["synthetic"]
    assert result["incomplete"] is False
    assert result["stop_reason"] is None
    assert len(result["tool_calls"]) == 1
    card = result["tool_calls"][0]
    assert card["status"] == "done"
    assert card["audit_dispatch"] == "enqueue_failed"

    from app.services.agent_runtime.tool_egress import sanitize_tool_call_record

    public_card = sanitize_tool_call_record(card)
    assert public_card["status"] == "done"
    assert public_card["audit_dispatch"] == "enqueue_failed"
