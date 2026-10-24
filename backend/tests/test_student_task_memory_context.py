"""Same-session recall projections; no live services or provider calls."""


def test_recall_marks_truncated_history_at_the_fake_model_boundary(monkeypatch):
    from app.services.agent_runtime import student_task
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime import experts_lead
    from types import SimpleNamespace

    captured = {}

    class FakeLLM:
        def chat(self, **kwargs):
            captured.update(kwargs)
            return "请补充被截断的细节。"

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: FakeLLM())
    history = [
        {"role": "user", "content": "合成编号 KITE-739 是蓝色。", "truncated": True},
        {"role": "assistant", "content": "已记录。"},
        {"role": "user", "content": "我刚才说的颜色是什么？"},
    ]
    staged = []
    monkeypatch.setattr(student_task, "TaskContextStore", lambda _mysql: SimpleNamespace(
        read=lambda *_args: {
            "active_run_id": "run-recall", "revision": 1, "focus": [],
            "latest_result": {}, "document_reads": [], "turns": [],
        },
        stage=lambda *args: staged.append(args[-1]),
    ))
    monkeypatch.setattr(student_task, "_lead_sources", lambda *_args: {
        "facts": {}, "candidates": [],
    })
    monkeypatch.setattr(student_task, "_recent", lambda *_args: {
        "messages": history, "coverage": "recent_discussion_only",
        "boundary": "same session only",
    })
    monkeypatch.setattr(experts_lead, "task_lead_decide", lambda *_args: {
        "agent": "conversation_recall", "goal_summary": "回忆本会话", "reply": "",
        "compared_roles": [], "document_action": "discuss",
    })

    def invoke(packet, blackboard, _session_id, **kwargs):
        return scoped_invoke(packet, blackboard, **kwargs)

    result = student_task.run_step(
        mysql=None,
        snapshot={"task_context_version": 0, "message": "我刚才说的颜色是什么？"},
        run_id="run-recall", owner="owner", session_id="session-a",
        repository=SimpleNamespace(record_business_delegation=lambda *_a, **_k: None),
        claim_token="claim", invoke=invoke, packet=None, board=Blackboard(),
    )

    sent = captured["messages"]
    assert "近期历史第1条消息只包含原消息开头" in sent[0]["content"]
    assert "不得依据省略部分推断" in sent[0]["content"]
    assert sent[1]["content"] == history[0]["content"]
    assert "truncated" not in sent[1]["content"]
    assert result["student_task"]["reply"] == "请补充被截断的细节。"
    assert result["student_task"]["resource_access"] == "conversation_only"
    assert result["tool_calls"] == []
    assert len(staged) == 1


def test_recall_truncation_positions_ignore_empty_and_non_chat_messages():
    from app.services.agent_runtime.student_task import _conversation_recall_appendix

    appendix = _conversation_recall_appendix([
        {"role": "user", "content": "完整消息。"},
        {"role": "assistant", "content": "", "truncated": True},
        {"role": "assistant", "content": "这条仅保留开头。", "truncated": True},
        {"role": "tool", "content": "internal", "truncated": True},
    ])

    assert "第2条消息只包含原消息开头" in appendix
    assert "第1条消息只包含原消息开头" not in appendix
    assert "不得依据省略部分推断" in appendix
