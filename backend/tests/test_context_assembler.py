from __future__ import annotations

from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.context_assembler import DefaultContextAssembler, TurnInputs
from app.services.agent_runtime.modes import InteractionMode


def _history(n: int):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"u{i}"})
        out.append({"role": "assistant", "content": f"a{i}"})
    return out


def test_ask_uses_k8_history_and_current_once():
    assembler = DefaultContextAssembler()
    current = "当前问题"
    ctx = assembler.assemble(
        TurnInputs(
            mode=InteractionMode.ASK,
            message=current,
            blackboard=Blackboard(),
            history=_history(10),
            conversation_summary="早期摘要",
        )
    )

    assert len(ctx.messages) == 17
    assert ctx.messages[-1] == {"role": "user", "content": current}
    assert sum(1 for m in ctx.messages if m["content"] == current) == 1
    assert "早期摘要" in ctx.system_tail


def test_ask_dedupes_current_if_history_already_contains_it():
    current = "当前问题"
    ctx = DefaultContextAssembler().assemble(
        TurnInputs(
            mode=InteractionMode.ASK,
            message=current,
            blackboard=Blackboard(),
            history=_history(10) + [{"role": "user", "content": current}],
        )
    )

    assert len(ctx.messages) <= 17
    assert ctx.messages[-1] == {"role": "user", "content": current}
    assert sum(1 for m in ctx.messages if m["content"] == current) == 1


def test_ask_memory_is_opt_in_to_assembler_inputs():
    assembler = DefaultContextAssembler()
    no_memory = assembler.assemble(
        TurnInputs(
            mode=InteractionMode.ASK,
            message="问问",
            blackboard=Blackboard(),
            memory_digest="",
        )
    )
    with_memory = assembler.assemble(
        TurnInputs(
            mode=InteractionMode.ASK,
            message="问问",
            blackboard=Blackboard(),
            memory_digest="长期记忆",
        )
    )

    assert "长期记忆" not in no_memory.system_tail
    assert "长期记忆" in with_memory.system_tail


def test_agent_router_uses_filled_card_only_and_no_history():
    bb = Blackboard()
    bb.write("portrait.current", {"summary": "画像已生成"}, "profile")
    ctx = DefaultContextAssembler().assemble(
        TurnInputs(
            mode=InteractionMode.AGENT,
            message="做人岗匹配",
            blackboard=bb,
            history=_history(10),
            conversation_summary="摘要",
        )
    )

    assert ctx.messages == []
    assert "画像已生成" in ctx.router_context
    assert "尚未生成" not in ctx.router_context
    assert "u9" not in ctx.router_context


def test_experts_memory_digest_capped_to_800_chars():
    memory = "记" * 1200
    ctx = DefaultContextAssembler().assemble(
        TurnInputs(
            mode=InteractionMode.EXPERTS,
            message="生成完整职业方案",
            blackboard=Blackboard(),
            memory_digest=memory,
        )
    )

    assert len(ctx.memory_digest) <= 800
    assert "记忆摘要" in ctx.router_context


def test_experts_receives_atomic_shared_evidence_with_read_only_rules():
    evidence = (
        '<SERVER_EVIDENCE kind="EvidencePack">\n'
        '{"sources":[{"uri":"career://me/resume","version":"resume-v3"}],'
        '"coverage":{"complete":false},"has_more":true,'
        '"continuations":[{"next_offset":10}]}\n'
        '</SERVER_EVIDENCE>'
    )
    ctx = DefaultContextAssembler().assemble(TurnInputs(
        mode=InteractionMode.EXPERTS,
        message="根据我已确认的简历做规划",
        blackboard=Blackboard(),
        evidence_context=evidence,
    ))

    assert ctx.evidence_context == evidence
    assert evidence in ctx.router_context
    assert "当前 Principal" in ctx.router_context
    assert "不授予业务工具或写入权限" in ctx.router_context
    assert "不得把局部资料当全文" in ctx.router_context


def test_experts_evidence_over_budget_is_not_half_json():
    evidence = '<SERVER_EVIDENCE kind="EvidencePack">' + ("x" * 7000)
    ctx = DefaultContextAssembler().assemble(TurnInputs(
        mode=InteractionMode.EXPERTS,
        message="请规划",
        blackboard=Blackboard(),
        evidence_context=evidence,
    ))

    assert "超过本轮独立证据预算" in ctx.evidence_context
    assert "x" * 100 not in ctx.evidence_context
    assert ctx.evidence_context in ctx.router_context


def test_agent_does_not_receive_experts_evidence_entry_by_accident():
    evidence = '<SERVER_EVIDENCE kind="EvidencePack">secret</SERVER_EVIDENCE>'
    ctx = DefaultContextAssembler().assemble(TurnInputs(
        mode=InteractionMode.AGENT,
        message="普通 Agent 请求",
        blackboard=Blackboard(),
        evidence_context=evidence,
    ))

    assert ctx.evidence_context == ""
    assert evidence not in ctx.router_context


def test_truncated_context_slots_are_labeled_incomplete_without_fake_continuation():
    from app.services.agent_runtime.budget import context_budget_for

    budget = context_budget_for(InteractionMode.ASK)
    ctx = DefaultContextAssembler().assemble(TurnInputs(
        mode=InteractionMode.ASK,
        message="请基于已有资料回答",
        blackboard=Blackboard(),
        conversation_summary="摘要" * (budget.summary.max_chars + 20),
        memory_digest="记忆" * (budget.memory_digest.max_chars + 20),
        attachment_context="附件" * (budget.attachment_context.max_chars + 20),
    ))

    assert ctx.truncated_slots == ["summary", "memory_digest", "attachment_context"]
    assert len(ctx.memory_digest) <= budget.memory_digest.max_chars
    assert len(ctx.attachment_context) <= budget.attachment_context.max_chars
    assert len(ctx.system_tail) > budget.summary.max_chars  # includes slot labels and safety text
    assert "剩余内容未进入本轮上下文" in ctx.system_tail
    assert "本上下文没有自动续读游标" in ctx.system_tail
    assert "不得据此认定资料不存在或已读完整" in ctx.attachment_context


def test_mode_runtime_marks_truncated_summary_as_partial_turn(monkeypatch):
    from app.services.agent_runtime.budget import context_budget_for
    from app.services.agent_runtime.egress_pipeline import project_turn
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.turn_contract import derive_turn_outcome

    captured = {}

    def run_ask(self, message, blackboard, prefix, hist, start, session_id,
                upgrade_hint, memory_mount, **kwargs):
        captured["context"] = kwargs["assembled_context"]
        return {"reply": "现有上下文里有一部分摘要没有带入。", "orchestration": {}}

    monkeypatch.setattr(ModeRuntime, "_run_ask", run_ask)
    result = ModeRuntime()._chat_impl(
        "请根据之前的偏好推荐",
        interaction_mode="ask",
        conversation_summary="历史摘要" * (context_budget_for(InteractionMode.ASK).summary.max_chars + 1),
        prepared_turn={
            "visible_text": "请根据之前的偏好推荐", "parts": [], "turn_file_ids": [],
            "mount_errors": [], "attach_card": "",
        },
    )

    assert "本上下文没有自动续读游标" in captured["context"].system_tail
    assert result["context_truncation"] == {
        "slots": ["summary"], "continuation_available": False,
    }
    outcome = derive_turn_outcome(result)
    assert outcome["status"] == "partial"
    assert outcome["reason"] == "context_truncated"
    assert outcome["next_action"] == "answer_in_session"
    assert outcome["resume_state"] == "not_available"
    public = project_turn(result).to_dict()
    assert public["outcome"]["reason"] == "context_truncated"
    assert public["outcome"]["context_slots"] == ["summary"]
    assert "历史摘要" * 100 not in str(public)
    assert "本上下文没有自动续读游标" not in str(public)
