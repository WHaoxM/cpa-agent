"""
Wrap langchain tools with PreToolUse + dual-channel projection.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import StructuredTool

from ...utils.logger import get_logger
from .modes import InteractionMode
from .pre_tool_use import GateContext, pre_tool_use
from .tool_audit import enqueue_audit
from .tool_factory import build_tools_for_subagent
from .tool_result_channels import build_model_view, build_ui_view
from .permissions import visible_tools_for
from .artifact_committer import get_artifact_committer

logger = get_logger("cpa-agent.agent_runtime.gated")


def _accepts_kwargs(fn) -> bool:
    try:
        import inspect
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in inspect.signature(fn).parameters.values()
        )
    except Exception:
        return False


def build_gated_tools(
    subagent: str,
    gate_ctx: GateContext,
    ui_collector: List[Dict[str, Any]],
) -> List[Any]:
    """
    Tools visible for this mode/subagent, each wrapped with gate + channels.
    Ask → empty list (model cannot see tools).
    """
    visible = visible_tools_for(
        gate_ctx.interaction_mode,
        subagent,
        plan_tool_allowlist=gate_ctx.plan_tool_allowlist,
    )
    if not visible:
        return []

    raw_tools = build_tools_for_subagent(
        subagent,
        student_id=gate_ctx.student_id,
        session_id=gate_ctx.session_id,
        extra_tools=(
            {"attachment_read"}
            if gate_ctx.plan_tool_allowlist and "attachment_read" in gate_ctx.plan_tool_allowlist
            else None
        ),
    )
    gated: List[Any] = []
    for tool in raw_tools:
        name = getattr(tool, "name", None) or ""
        if name not in visible:
            continue
        gated.append(_wrap_one(tool, gate_ctx, ui_collector))
    return gated


def _wrap_one(
    base_tool: Any,
    gate_ctx: GateContext,
    ui_collector: List[Dict[str, Any]],
) -> Any:
    name = str(getattr(base_tool, "name", "tool"))
    description = str(getattr(base_tool, "description", name) or name)
    args_schema = getattr(base_tool, "args_schema", None)

    def gated_func(**kwargs: Any) -> str:
        gate = pre_tool_use(name, kwargs, gate_ctx)
        if not gate.allowed:
            msg = f"[permission_denied] {gate.verdict.reason}"
            ui_collector.append(
                build_ui_view(name, msg, parameters=gate.args, status="error")
            )
            enqueue_audit(
                session_id=gate_ctx.session_id,
                student_id=gate_ctx.student_id,
                tool_name=name,
                decision="deny",
                args=gate.args,
                result_text=msg,
                reason=gate.verdict.reason,
            )
            return build_model_view(name, msg)

        raw: Any
        try:
            # Prefer func(**args): StructuredTool.invoke() strips keys not in
            # args_schema, which drops PreToolUse-injected student_id/session_id.
            func = getattr(base_tool, "func", None)
            if callable(func):
                raw = func(**gate.args)
            elif hasattr(base_tool, "run"):
                raw = base_tool.run(**gate.args) if _accepts_kwargs(base_tool.run) else base_tool.run(gate.args)
            elif hasattr(base_tool, "invoke"):
                raw = base_tool.invoke(gate.args)
            else:
                raw = f"[error] cannot invoke tool {name}"
        except TypeError as te:
            # Fallback: schema-visible keys only (injected keys may not match signature)
            try:
                fields = getattr(args_schema, "model_fields", None) or {}
                slim = {k: v for k, v in gate.args.items() if not fields or k in fields}
                if callable(func):
                    raw = func(**slim)
                elif hasattr(base_tool, "invoke"):
                    raw = base_tool.invoke(slim)
                else:
                    raise te
            except Exception as e:
                logger.warning("gated tool %s failed: %s", name, e)
                raw = f"[error] {name} 执行失败"
        except Exception as e:
            logger.warning("gated tool %s failed: %s", name, e)
            raw = f"[error] {name} 执行失败"

        text = raw if isinstance(raw, str) else str(raw)
        model_view = build_model_view(name, text)
        ui_view = build_ui_view(name, raw, parameters=gate.args, status="done")
        candidates = ui_view.pop("artifact_candidates", [])
        artifact_refs = get_artifact_committer().commit_candidates(
            candidates,
            student_id=gate_ctx.student_id,
            session_id=gate_ctx.session_id,
        )
        if artifact_refs:
            ui_view["artifact_refs"] = artifact_refs
        ui_collector.append(ui_view)
        enqueue_audit(
            session_id=gate_ctx.session_id,
            student_id=gate_ctx.student_id,
            tool_name=name,
            decision="allow",
            args=gate.args,
            result_text=text[:2000],
            reason="ok" if not gate.rewritten else "student_id_rewritten",
        )
        return model_view

    if args_schema is not None:
        return StructuredTool.from_function(
            func=gated_func,
            name=name,
            description=description,
            args_schema=args_schema,
        )
    return StructuredTool.from_function(
        func=gated_func,
        name=name,
        description=description,
    )


def make_gate_context(
    *,
    interaction_mode: InteractionMode,
    subagent: str,
    session_id: Optional[str] = None,
    student_id: Optional[str] = None,
    plan_tool_allowlist: Optional[set] = None,
) -> GateContext:
    return GateContext(
        interaction_mode=interaction_mode,
        subagent=subagent,
        session_id=session_id,
        student_id=student_id,
        plan_tool_allowlist=plan_tool_allowlist,
    )
