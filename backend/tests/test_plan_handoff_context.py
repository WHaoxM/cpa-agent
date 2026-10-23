"""Later Experts steps retain the bounded current task, not chat history."""

import pytest

from app.services.agent_runtime.blackboard import default_read_keys, default_write_keys
from app.services.agent_runtime.plan import PLAN_TEMPLATES, build_plan_packets


BRIEF = "目标是杭州的数据分析岗位；只考虑本地工作，学习计划限定为六周。"


@pytest.mark.parametrize("template", sorted(PLAN_TEMPLATES))
@pytest.mark.parametrize("source", ["handoff_brief", "goal_summary", "user_utterance"])
def test_every_step_retains_bounded_task_brief_without_expanding_access(template, source):
    kwargs = {"user_utterance": "原始对话噪声，不应重复进入瘦包"}
    kwargs[source] = BRIEF
    packets = build_plan_packets(template, **kwargs)

    for index, packet in enumerate(packets):
        assert BRIEF in packet.goal
        assert packet.plan_step_id == f"{template}:{index}:{packet.subagent}"
        assert packet.read_keys == default_read_keys(packet.subagent)
        assert packet.write_keys == default_write_keys(packet.subagent)
        if index:
            assert packet.lean is True
            assert packet.user_utterance == ""
            assert packet.success_criteria == ""
            assert "原始对话噪声" not in packet.goal


def test_handoff_precedence_and_existing_size_boundary_are_preserved():
    packets = build_plan_packets(
        "end_to_end", "raw-utterance", handoff_brief="B" * 500,
        goal_summary="fallback-summary",
    )
    for packet in packets:
        assert packet.goal.endswith("B" * 400)
        assert "B" * 401 not in packet.goal
        assert "fallback-summary" not in packet.goal


@pytest.mark.parametrize("template", sorted(PLAN_TEMPLATES))
def test_empty_brief_keeps_step_only_goals(template):
    packets = build_plan_packets(template, "")
    role_labels = {
        "profile": "画像",
        "match": "匹配",
        "report": "报告",
        "planner": "规划",
        "qa": "问答",
    }
    for index, packet in enumerate(packets):
        assert packet.goal == (
            f"执行计划步骤 {index + 1}/{len(packets)}"
            f"（{role_labels[packet.subagent]}）。"
        )
