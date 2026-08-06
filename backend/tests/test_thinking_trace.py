from app.services.agent_runtime.thinking_egress import (
    looks_like_playbook,
    sanitize_thinking_steps,
)
from app.services.agent_runtime.thinking_trace import (
    ask_fallback_trace,
    parse_thinking_answer,
    synthesize_agent_trace,
    synthesize_ask_ui_thinking,
)


def test_parse_thinking_answer_splits_blocks():
    raw = """
<<<THINKING>>>
用户在探能力边界。

我这轮没有他的画像，只能先讲清楚范围。

我会先给能力地图，再问一个具体问题。
<<<ANSWER>>>
你好，我是职导星图助手。
"""
    steps, answer = parse_thinking_answer(raw)
    assert len(steps) >= 3
    assert answer.startswith("你好")


def test_parse_thinking_answer_missing_block():
    steps, answer = parse_thinking_answer("直接一段回复")
    assert steps == []
    assert answer is None


def test_ask_ui_thinking_is_deliberation_not_playbook():
    steps = synthesize_ask_ui_thinking(
        "帮我看看方向",
        path="ask_attachments",
        filenames=["最新简历.pdf"],
        mount_ok=True,
    )
    assert len(steps) >= 4
    blob = "\n".join(steps)
    assert "最新简历.pdf" in blob or "《最新简历.pdf》" in blob
    assert "先确认附件是否可读" not in blob
    assert "轻量模式下可" not in blob
    assert "完整管线仍需" not in blob
    assert "正式回复应" not in blob
    assert all(not looks_like_playbook(s) for s in steps)


def test_playbook_lines_dropped_by_egress():
    dirty = [
        "用户附带了材料。先确认附件是否可读，再决定给框架建议还是基于摘要作答。",
        "轻量模式下可阅读已挂载附件摘要；完整管线仍需执行模式。",
        "正式回复应先说明需要个人画像。",
        "我看到用户挂了《最新简历.pdf》，先核对本轮摘要是否可读。",
    ]
    clean = sanitize_thinking_steps(dirty)
    blob = "\n".join(clean)
    assert "先确认附件是否可读" not in blob
    assert "轻量模式下可" not in blob
    assert "正式回复应" not in blob
    assert "最新简历.pdf" in blob


def test_ask_fallback_alias_uses_ui_synthesizer():
    steps = ask_fallback_trace("你好，告诉我你的能力", path="ask_capability")
    assert len(steps) >= 4
    assert any("我" in s for s in steps)


def test_synthesize_agent_includes_tools():
    steps = synthesize_agent_trace(
        message="帮我看看适合什么岗位",
        strategy="agent",
        active_subagent="match_subagent",
        tool_calls=[{"tool_name": "graph_query", "summary": "查了岗位技能边"}],
        supervisor_view={"allow_tools": True, "confidence": 0.7},
    )
    assert len(steps) >= 4
    blob = "\n".join(steps)
    assert "知识图谱查询" in blob
    assert "graph_query" not in blob
