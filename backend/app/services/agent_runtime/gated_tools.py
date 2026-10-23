"""
Wrap langchain tools with PreToolUse + dual-channel projection.
"""

from __future__ import annotations

import json
import inspect
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

# Only repeat idempotent reads. Mutating pipeline/report tools must never be
# re-executed merely because their response was lost after a timeout.
_TRANSIENT_RETRY_READS = frozenset({
    "attachment_read", "career_data_read", "component_snapshot_read",
    "component_snapshot_discover", "conversation_history_lookup",
    "graph_query", "semantic_search",
})


def _egress_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Avoid retaining outward search terms in audit/UI parameter payloads."""
    if tool_name != "public_web_search":
        return args
    query = args.get("query")
    return {
        "query": "[redacted before audit/UI persistence]",
        "query_chars": len(query) if isinstance(query, str) else 0,
        "max_results": args.get("max_results"),
    }


def _failure(code: str, *, retryable: bool, attempts: int) -> Dict[str, Any]:
    return {
        "status": "error", "success": False, "error_code": code,
        "retryable": retryable, "attempts": attempts,
        "summary": "工具执行未完成，请根据错误状态调整下一步；不得声称操作成功。",
    }


def _transient(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError))


def _error_code(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, TimeoutError):
        return "tool_timeout"
    if isinstance(exc, ConnectionError):
        return "tool_connection_error"
    return "tool_execution_error"


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
            ({name for name in ("attachment_read", "component_snapshot_read",
                                "component_snapshot_discover", "conversation_history_lookup",
                                "public_web_search")
              if name in gate_ctx.plan_tool_allowlist}
             if gate_ctx.plan_tool_allowlist else None)
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
            raw = _failure("permission_denied", retryable=False, attempts=0)
            msg = json.dumps(raw, ensure_ascii=False)
            ui_collector.append(
                build_ui_view(name, raw, parameters=_egress_tool_args(name, gate.args), status="error")
            )
            enqueue_audit(
                session_id=gate_ctx.session_id,
                student_id=gate_ctx.student_id,
                tool_name=name,
                decision="deny",
                args=_egress_tool_args(name, gate.args),
                result_text=msg,
                reason=gate.verdict.reason,
            )
            return build_model_view(name, msg)

        raw: Any
        execution_status = "done"
        attempts = 0

        def invoke_once() -> Any:
            # Prefer func(**args): StructuredTool.invoke() strips keys not in
            # args_schema, which drops PreToolUse-injected student_id/session_id.
            func = getattr(base_tool, "func", None)
            if callable(func):
                # Check *binding* before invocation. Catching an execution
                # TypeError and calling again can duplicate side effects.
                call_args = gate.args
                try:
                    signature = inspect.signature(func)
                    signature.bind_partial(**call_args)
                except TypeError:
                    fields = getattr(args_schema, "model_fields", None) or signature.parameters
                    call_args = {k: v for k, v in gate.args.items() if k in fields}
                except ValueError:
                    pass  # Some native callables expose no inspectable signature.
                return func(**call_args)
            if hasattr(base_tool, "run"):
                return base_tool.run(**gate.args) if _accepts_kwargs(base_tool.run) else base_tool.run(gate.args)
            if hasattr(base_tool, "invoke"):
                return base_tool.invoke(gate.args)
            raise NotImplementedError("tool has no callable executor")

        while True:
            attempts += 1
            try:
                raw = invoke_once()
                break
            except Exception as e:
                retryable = name in _TRANSIENT_RETRY_READS and _transient(e)
                if retryable and attempts == 1:
                    logger.warning("gated read tool %s transient failure; retrying once", name)
                    continue
                logger.warning("gated tool %s failed: %s", name, type(e).__name__)
                raw = _failure(_error_code(e), retryable=retryable, attempts=attempts)
                execution_status = "error"
                break

        if attempts > 1 and isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    raw = {**parsed, "attempts": attempts}
            except ValueError:
                pass
        elif attempts > 1 and isinstance(raw, dict):
            raw = {**raw, "attempts": attempts}

        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        model_view = build_model_view(name, raw)
        persisted_args = _egress_tool_args(name, gate.args)
        ui_view = build_ui_view(name, raw, parameters=persisted_args, status=execution_status)
        candidates = ui_view.pop("artifact_candidates", [])
        artifact_refs = []
        if gate_ctx.allow_artifact_commit:
            artifact_refs = get_artifact_committer().commit_candidates(
                candidates,
                student_id=gate_ctx.student_id,
                session_id=gate_ctx.session_id,
            )
        elif candidates:
            # Durable read-only plans must not gain artifact-write side effects
            # through a tool-result channel, even if a future tool adds one.
            logger.warning(
                "plan-scoped artifact candidates suppressed: tool=%s count=%s",
                name, min(len(candidates), 8),
            )
        if artifact_refs:
            ui_view["artifact_refs"] = artifact_refs
        ui_collector.append(ui_view)
        enqueue_audit(
            session_id=gate_ctx.session_id,
            student_id=gate_ctx.student_id,
            tool_name=name,
            decision="allow",
            args=persisted_args,
            result_text=text[:2000],
            reason="ok" if not gate.rewritten else "student_id_rewritten",
        )
        return model_view

    # Sync LangGraph ToolNode may execute tools on a worker thread. Propagate
    # only the already-authenticated internal run claim (never HTTP g/request
    # state) so owner-bound tools can revalidate Principal and auth_epoch.
    from ...api._helpers import (
        capture_trusted_agent_worker_scope,
        run_with_trusted_agent_worker_scope,
    )

    worker_identity = capture_trusted_agent_worker_scope()
    from ..career_exploration_analysis import capture_read_scope, read_scope
    exploration_scope = capture_read_scope()
    if worker_identity is not None:
        original_gated_func = gated_func

        def gated_func(**kwargs: Any) -> str:
            if exploration_scope is not None:
                with read_scope(exploration_scope):
                    return run_with_trusted_agent_worker_scope(worker_identity, original_gated_func, **kwargs)
            return run_with_trusted_agent_worker_scope(worker_identity, original_gated_func, **kwargs)

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
    allow_artifact_commit: bool = True,
) -> GateContext:
    return GateContext(
        interaction_mode=interaction_mode,
        subagent=subagent,
        session_id=session_id,
        student_id=student_id,
        plan_tool_allowlist=plan_tool_allowlist,
        allow_artifact_commit=allow_artifact_commit,
    )
