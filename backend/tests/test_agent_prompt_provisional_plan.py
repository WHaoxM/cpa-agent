"""A broad learning-plan request can complete as advice without fake artifacts."""

from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.mode_runtime import ModeRuntime
from app.services.agent_runtime.prompt_selector import PromptSelectionKey, get_prompt_selector
from app.services.agent_runtime.scoped_invoke import _build_subagent_system
from app.services.agent_runtime.task_packet import build_standard_packet
from app.services.agent_runtime.turn_contract import derive_turn_outcome
from app.services.agent_runtime.modes import InteractionMode


def test_live_prompt_route_separates_provisional_plan_from_match_gate():
    router = get_prompt_selector().select(
        PromptSelectionKey("supervisor", InteractionMode.AGENT, "router"),
        variables={"router_context": "【黑板能力卡】\n- （暂无已生成业务态）", "message": "制定本周学习计划"},
    ).system
    packet = build_standard_packet("planner", "制定本周学习计划")
    planner = _build_subagent_system("planner", packet, {}, interaction_mode=InteractionMode.AGENT)

    assert "学习计划" in router and "选 planner" in router
    assert "缺少画像前置时，匹配和报告请求" in router
    assert "直接给可调整的本周初稿" in planner
    assert "无 portrait.current 不得执行匹配" in planner
    assert Blackboard().check_gate("planner") is None
    assert Blackboard().check_gate("match") is not None


def test_agent_learning_plan_without_owner_cannot_claim_saved_artifact(monkeypatch):
    routed_prompts = []
    packets = []

    class RouterLLM:
        def chat_json(self, **kwargs):
            routed_prompts.append(kwargs["messages"][0]["content"])
            return {
                "action": "call", "subagent": "planner", "goal": "制定本周学习计划",
                "document_view": "answer", "confidence": 0.9,
            }

    def invoke(packet, blackboard, _session_id, **_kwargs):
        packets.append(packet)
        assert not blackboard.has("portrait.current")
        return {
            "reply": "暂按每天 30 分钟：周一梳理目标，周二至周四完成一个练习，周五复盘并留下作品。你本周每天能投入多久？",
            "tool_calls": [],
        }

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: RouterLLM())
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime._invoke_subagent", invoke)

    result = ModeRuntime().chat("制定本周学习计划", interaction_mode="agent")

    assert len(routed_prompts) == 1
    assert "制定本周学习计划" in routed_prompts[0]
    assert [packet.subagent for packet in packets] == ["planner"]
    assert packets[0].write_keys == ["plan.current"]
    assert result["orchestration"]["plan_results"][0]["ok"] is False
    assert "plan.current" not in result["blackboard"]
    assert derive_turn_outcome(result)["status"] == "partial"
    assert "周一" in result["reply"]
