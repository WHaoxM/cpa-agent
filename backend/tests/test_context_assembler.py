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
