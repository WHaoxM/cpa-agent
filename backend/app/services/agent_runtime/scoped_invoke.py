"""
Scoped subagent invoke with PreToolUse + dual-channel tools.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, Dict, List, Optional, Set, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages


def _require_tool_node():
    """langgraph>=0.2 exports ToolNode; ancient 0.0.x only has ToolExecutor."""
    try:
        from langgraph.prebuilt import ToolNode, tools_condition
        return ToolNode, tools_condition
    except ImportError as e:
        raise ImportError(
            "当前环境的 langgraph 过旧，缺少 ToolNode。"
            "请在 mkg 中执行: pip install \"langgraph>=0.4.0\" "
            f"(原始错误: {e})"
        ) from e

from ...utils.logger import get_logger
from .blackboard import Blackboard
from .budget import CHARS_BLACKBOARD_CARD, truncate_slot
from .gated_tools import build_gated_tools, make_gate_context
from .modes import InteractionMode
from .prompt_selector import PromptSelectionKey, get_prompt_selector
from .task_packet import TaskPacket
from .tool_egress import redact_text

logger = get_logger("cpa-agent.agent_runtime.scoped")


class MiniState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]


def _build_subagent_system(
    subagent: str,
    packet: TaskPacket,
    slice_data: Dict[str, Any],
    *,
    interaction_mode: InteractionMode = InteractionMode.AGENT,
) -> str:
    # Ask channel must never see Agent Work Manual (tool/blackboard jargon).
    content_kind = (
        "attachment"
        if interaction_mode == InteractionMode.ASK and subagent == "qa"
        else "text"
    )
    role = get_prompt_selector().select(
        PromptSelectionKey(
            engine="subagent",
            interaction_mode=interaction_mode,
            content_kind=content_kind,
            subagent=subagent,
        )
    ).system

    packet_view = {
        "subagent": packet.subagent,
        "goal": truncate_slot(packet.goal, 240),
        "read_keys": list(packet.read_keys),
        "write_keys": [] if interaction_mode == InteractionMode.ASK else list(packet.write_keys),
    }
    if packet.plan_step_id:
        packet_view["plan_step_id"] = packet.plan_step_id
    if packet.constraints_refs:
        packet_view["constraints_refs"] = list(packet.constraints_refs)
    if packet.success_criteria and interaction_mode != InteractionMode.ASK:
        packet_view["success_criteria"] = truncate_slot(packet.success_criteria, 160)
    packet_json = json.dumps(packet_view, ensure_ascii=False, indent=2)
    slice_json = json.dumps(slice_data, ensure_ascii=False, indent=2)
    if len(slice_json) > CHARS_BLACKBOARD_CARD:
        slice_json = truncate_slot(slice_json, CHARS_BLACKBOARD_CARD)

    rules = (
        "规则: 只使用已绑定工具；不要编造未提供的业务 id；"
        "禁止在回复中复述电话、邮箱或完整 JSON 原文。"
    )
    if interaction_mode != InteractionMode.ASK:
        rules += (
            "完成后用自然语言总结结果，并在需要时给出可写回的 JSON 块"
            '（格式: ```blackboard\\n{"key": {...}}\\n```，key 必须在 write_keys 内）。'
        )
    else:
        rules += "用自然语言回答用户；不要输出内部工具名或状态键。"

    return (
        f"{role}\n\n"
        f"【TaskPacket】\n{packet_json}\n\n"
        f"【允许读取的会话切片】\n{slice_json}\n\n"
        f"{rules}"
    )


def _extract_blackboard_writes(text: str, write_keys: List[str]) -> Dict[str, Any]:
    if not text or "```blackboard" not in text:
        return {}
    try:
        start = text.index("```blackboard") + len("```blackboard")
        end = text.index("```", start)
        raw = text[start:end].strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if k in write_keys}
    except Exception:
        return {}


def scoped_invoke(
    packet: TaskPacket,
    blackboard: Blackboard,
    *,
    temperature: float = 0.4,
    max_tokens: int = 2048,
    allow_tools: bool = True,
    interaction_mode: InteractionMode = InteractionMode.AGENT,
    session_id: Optional[str] = None,
    student_id: Optional[str] = None,
    plan_tool_allowlist: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    start = time.time()
    subagent = packet.subagent
    # Model gets projected pointers only (not full constraint/resume blobs)
    slice_data = blackboard.read_slice_for_model(packet.read_keys)
    system = _build_subagent_system(
        subagent, packet, slice_data, interaction_mode=interaction_mode,
    )
    if packet.lean:
        user_content = packet.goal
    elif packet.user_utterance and packet.user_utterance != packet.goal:
        user_content = f"用户原话：{packet.user_utterance}\n\n任务正文：\n{packet.goal}"
    else:
        user_content = packet.goal

    ui_collector: List[Dict[str, Any]] = []
    gate_ctx = make_gate_context(
        interaction_mode=interaction_mode,
        subagent=subagent,
        session_id=session_id,
        student_id=student_id,
        plan_tool_allowlist=plan_tool_allowlist,
    )

    # Ask with empty allowlist → no tools; Ask + {attachment_read} → gated tools
    ask_no_tools = (
        interaction_mode == InteractionMode.ASK
        and not (plan_tool_allowlist and len(plan_tool_allowlist) > 0)
    )
    if not allow_tools or ask_no_tools:
        from ...utils.llm_client import get_llm_client

        llm = get_llm_client()
        reply = llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            max_tokens=min(max_tokens, 1024),
        ) or "（无工具轻量回复）"
        return {
            "reply": redact_text(reply, max_len=8000),
            "tool_calls": [],
            "blackboard_updates": {},
            "elapsed_seconds": round(time.time() - start, 2),
            "subagent": subagent,
            "engine": "scoped_chat",
            "permission_denials": list(gate_ctx.denials),
        }

    from ..langgraph_agent import _build_llm_with_tools

    lc_tools = build_gated_tools(subagent, gate_ctx, ui_collector)
    if not lc_tools:
        from ...utils.llm_client import get_llm_client

        llm = get_llm_client()
        reply = llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        ) or "当前模式无可用工具。"
        return {
            "reply": redact_text(reply or "", max_len=8000),
            "tool_calls": [],
            "blackboard_updates": _extract_blackboard_writes(reply or "", packet.write_keys),
            "elapsed_seconds": round(time.time() - start, 2),
            "subagent": subagent,
            "engine": "scoped_invoke",
            "permission_denials": list(gate_ctx.denials),
        }

    ToolNode, tools_condition = _require_tool_node()
    llm_with_tools = _build_llm_with_tools(lc_tools, temperature, max_tokens)
    tool_node = ToolNode(lc_tools, handle_tool_errors=True)

    def agent_node(state: MiniState) -> Dict:
        msgs = list(state["messages"])
        msgs = [SystemMessage(content=system)] + [
            m for m in msgs if not isinstance(m, SystemMessage)
        ]
        response = llm_with_tools.invoke(msgs)
        return {"messages": [response]}

    wf = StateGraph(MiniState)
    wf.add_node("agent", agent_node)
    wf.add_node("tools", tool_node)
    wf.set_entry_point("agent")
    wf.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    wf.add_edge("tools", "agent")
    graph = wf.compile()

    result = graph.invoke({"messages": [HumanMessage(content=user_content)]})
    messages = result.get("messages") or []
    final = messages[-1] if messages else None
    reply = getattr(final, "content", None) or "抱歉，子 Agent 未能生成回复。"

    # Prefer ui_collector (dual-channel); fall back empty
    tool_calls = list(ui_collector)
    if not tool_calls:
        for msg in messages:
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    tool_calls.append({
                        "tool_name": tc["name"],
                        "parameters": tc.get("args", {}),
                        "summary": f"{tc['name']} 已调用",
                        "result_preview": f"{tc['name']} 已调用",
                    })

    updates = _extract_blackboard_writes(str(reply), packet.write_keys)
    return {
        "reply": redact_text(str(reply), max_len=8000),
        "tool_calls": tool_calls,
        "blackboard_updates": updates,
        "elapsed_seconds": round(time.time() - start, 2),
        "subagent": subagent,
        "engine": "scoped_invoke",
        "permission_denials": list(gate_ctx.denials),
    }
