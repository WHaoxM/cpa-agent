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
    ToolMessage,
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
    if subagent == "planner" and interaction_mode != InteractionMode.ASK:
        rules += (
            "计划正文完成后由服务端保存和核验；你只需自然语言交付计划。"
            "不要输出 blackboard JSON 块、虚构保存状态或内部键名。"
        )
    elif interaction_mode != InteractionMode.ASK:
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

    # Bound the graph loop. At the limit ask for a text-only conclusion, but
    # never mutate an AIMessage to erase an unexecuted tool call silently.
    max_iterations = 6
    iteration_count = {"n": 0}
    iteration_limit = {"reached": False}
    tool_failures: Dict[str, int] = {}
    tool_error_codes: List[str] = []

    def agent_node(state: MiniState) -> Dict:
        iteration_count["n"] += 1
        msgs = list(state["messages"])
        last_step = iteration_count["n"] >= max_iterations
        system_text = system
        if last_step:
            # OpenCode's max-step contract prompts for a final text answer.
            # Some providers can still return tool_calls, so guard below too.
            system_text += (
                "\n\n本轮已到工具调用步骤上限。不要再调用工具；"
                "只用文字说明已完成的工作、未完成的部分，并提示用户如何继续。"
            )
        msgs = [SystemMessage(content=system_text)] + [
            m for m in msgs if not isinstance(m, SystemMessage)
        ]
        response = llm_with_tools.invoke(msgs)
        if last_step and (getattr(response, "tool_calls", None) or not str(response.content or "").strip()):
            iteration_limit["reached"] = True
            logger.warning("tool_iteration_limit: last model step did not produce a text answer")
            # A tool-call-only AIMessage must not become a pretend successful
            # answer. Return a new explicit partial answer without executing it.
            return {"messages": [AIMessage(content=(
                f"本轮已执行 {max_iterations - 1} 轮工具步骤，但达到步骤上限。"
                "最新请求的工具操作尚未执行，无法确认最终结果；"
                "请回复“继续”，我会接着处理。"
            ))]}
        return {"messages": [response]}

    def _retry_tool_node(state: MiniState) -> Dict:
        """Observe the ToolNode's actual ToolMessage settlements."""
        result = tool_node.invoke(state)
        msgs = result.get("messages", [])
        for msg in msgs:
            if not isinstance(msg, ToolMessage):
                continue
            content = str(msg.content or "")
            try:
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    payload = {}
            except ValueError:
                payload = {}
            tool_name = str(getattr(msg, "name", None) or "unknown")
            result_status = str(payload.get("status") or "").lower()
            failed = (
                getattr(msg, "status", None) == "error"
                or result_status in {"error", "failed", "denied", "unavailable"}
                or (
                    payload.get("success") is False
                    and result_status not in {"needs_input", "approval_required", "partial", "running", "pending", "queued"}
                )
            )
            # The Document Access tool has its own page/window contract; never
            # apply a generic character cut to that structured response.
            if len(content) > 8000 and tool_name != "attachment_read":
                msg.content = json.dumps({
                    "status": "partial", "success": False,
                    "error_code": "tool_output_truncated", "retryable": False,
                    "truncated": True,
                    "continuation": "请缩小查询范围或按工具分页继续读取。",
                }, ensure_ascii=False)
                if ui_collector:
                    ui_collector[-1].update({
                        "status": "partial", "truncated": True,
                        "continuation": "请缩小查询范围或按工具分页继续读取。",
                    })
                failed = True
                payload = {"error_code": "tool_output_truncated"}
            if failed:
                tool_failures[tool_name] = tool_failures.get(tool_name, 0) + 1
                tool_error_codes.append(str(payload.get("error_code") or "tool_execution_error"))
                logger.warning("tool settlement failed: %s (%s)", tool_name, tool_error_codes[-1])
        return result

    def _check_continue(state: MiniState) -> str:
        """检查是否需要继续循环。"""
        if iteration_count["n"] >= max_iterations:
            return END
        msgs = state.get("messages", [])
        if not msgs:
            return END
        last = msgs[-1]
        # 最后一条是 AI 消息且无 tool_calls → 结束
        if isinstance(last, AIMessage):
            if not getattr(last, "tool_calls", None):
                return END
        return "tools"

    wf = StateGraph(MiniState)
    wf.add_node("agent", agent_node)
    wf.add_node("tools", _retry_tool_node)
    wf.set_entry_point("agent")
    wf.add_conditional_edges("agent", _check_continue, {"tools": "tools", END: END})
    wf.add_edge("tools", "agent")
    graph = wf.compile()

    try:
        result = graph.invoke({"messages": [HumanMessage(content=user_content)]})
    except RuntimeError as exc:
        reason = str(exc)
        if reason != "turn_token_cap_exceeded" and not reason.startswith("turn_token_usage_unverified"):
            raise
        # Earlier tool reads may have succeeded, but no final model answer was
        # produced. Preserve their UI records and never turn a budget stop into
        # either HTTP 500 or a fabricated completed business outcome.
        return {
            "reply": "本轮已达到模型调用预算，尚未形成完整结论。已读取的资料不会被当作正式分析结果；请回复“继续”以接着处理。",
            "incomplete": True,
            "stop_reason": reason,
            "tool_calls": list(ui_collector),
            "blackboard_updates": {},
        }
    messages = result.get("messages") or []
    final = messages[-1] if messages else None
    reply = getattr(final, "content", None) or "抱歉，子 Agent 未能生成回复。"
    tool_failed = bool(tool_failures)
    if tool_failed:
        reply = "本轮工具执行未完成，以下回答不能视为已核验成果。\n" + str(reply)

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
    if tool_failed and not any(
        str(call.get("status") or "").lower() in {"error", "failed", "partial"}
        for call in tool_calls if isinstance(call, dict)
    ):
        tool_calls.append({
            "tool_name": next(iter(tool_failures)), "status": "error",
            "error_code": tool_error_codes[-1], "retryable": False,
            "summary": "工具执行未完成", "result_preview": "工具执行未完成",
        })

    updates = {} if tool_failed else _extract_blackboard_writes(str(reply), packet.write_keys)
    return {
        "reply": redact_text(str(reply), max_len=8000),
        "incomplete": iteration_limit["reached"] or tool_failed,
        "stop_reason": "tool_iteration_limit" if iteration_limit["reached"] else ("tool_failed" if tool_failed else None),
        "tool_calls": tool_calls,
        "blackboard_updates": updates,
        "elapsed_seconds": round(time.time() - start, 2),
        "subagent": subagent,
        "engine": "scoped_invoke",
        "permission_denials": list(gate_ctx.denials),
    }
