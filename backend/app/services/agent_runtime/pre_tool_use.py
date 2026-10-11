"""
PreToolUse gate: visibility + permission + session student_id binding.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional, Set

from .modes import InteractionMode
from .permissions import (
    STUDENT_SCOPED_TOOLS,
    Decision,
    PermissionVerdict,
    evaluate,
    visible_tools_for,
)  # PermissionVerdict used for deny construction


@dataclass
class GateContext:
    interaction_mode: InteractionMode
    subagent: str
    session_id: Optional[str] = None
    student_id: Optional[str] = None
    plan_tool_allowlist: Optional[Set[str]] = None
    denials: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class GateResult:
    allowed: bool
    args: Dict[str, Any]
    verdict: PermissionVerdict
    rewritten: bool = False


_STUDENT_ID_KEYS = frozenset({"student_id", "studentid", "user_id", "userid"})
_SESSION_ID_KEYS = frozenset({"session_id", "sessionid", "conversation_id", "conversationid"})


class ToolScopeNormalizer:
    """Normalize model-supplied tool args to the trusted request/session scope."""

    def __init__(
        self,
        tool_name: str,
        args: Dict[str, Any],
        *,
        student_id: Optional[str],
        session_id: Optional[str],
    ):
        self.tool_name = tool_name
        self.args = deepcopy(args) if isinstance(args, dict) else {}
        self.student_id = student_id
        self.session_id = session_id
        self.rewritten = False

    def normalize(self) -> tuple[Dict[str, Any], bool, Optional[str]]:
        if self.tool_name not in STUDENT_SCOPED_TOOLS:
            return self.args, False, None

        deny = self._missing_scope_reason()
        if deny:
            return self.args, False, deny

        # pipeline_run accepts params as a model-writable JSON string. Decode it
        # before applying the same scope normalization used for nested objects.
        if self.tool_name == "pipeline_run" and "params" in self.args:
            raw_params = self.args["params"]
            params_was_string = isinstance(raw_params, str)
            if isinstance(raw_params, str):
                try:
                    raw_params = json.loads(raw_params)
                except (TypeError, ValueError):
                    return self.args, False, "pipeline params must be a JSON object"
            if not isinstance(raw_params, dict):
                return self.args, False, "pipeline params must be a JSON object"
            self.args["params"] = raw_params
        out = self._rewrite_value(self.args)
        if self.tool_name == "pipeline_run":
            params = out.setdefault("params", {})
            if params.get("student_id") != self.student_id:
                self.rewritten = True
                params["student_id"] = self.student_id
            # Preserve the caller's representation: LangChain uses a string,
            # while direct gate users can pass an already-decoded object.
            if "params" in self.args and params_was_string:
                out["params"] = json.dumps(params, ensure_ascii=False)
        if self.tool_name == "portrait_generate" and out.get("entity_type") == "student":
            # `name` was historically passed as student_id by the tool.
            if out.get("name") != self.student_id:
                self.rewritten = True
                out["name"] = self.student_id
        if self.student_id is not None and out.get("student_id") != self.student_id:
            self.rewritten = True
            out["student_id"] = self.student_id
        if self.session_id and self._should_bind_session(out):
            if out.get("session_id") != self.session_id:
                self.rewritten = True
                out["session_id"] = self.session_id
        return out, self.rewritten, None

    def _missing_scope_reason(self) -> Optional[str]:
        if self.student_id:
            return None
        if self.tool_name in ("attachment_read", "career_data_read"):
            return "missing session student_id for scoped tool"
        if self.tool_name in ("db_query", "pipeline_run", "portrait_generate", "report_generate"):
            q = str(self.args.get("query", ""))
            if (
                self.tool_name != "db_query"
                or "student" in q.lower()
                or self.args.get("table_hint") == "student_profile"
            ):
                return "missing session student_id for scoped tool"
        return None

    def _should_bind_session(self, out: Dict[str, Any]) -> bool:
        return bool(self.session_id)

    def _rewrite_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for key, inner in value.items():
                norm = str(key).replace("-", "_").lower()
                if norm in _STUDENT_ID_KEYS and self.student_id is not None:
                    if inner != self.student_id:
                        self.rewritten = True
                    out[key] = self.student_id
                elif norm in _SESSION_ID_KEYS and self.session_id:
                    if inner != self.session_id:
                        self.rewritten = True
                    out[key] = self.session_id
                else:
                    out[key] = self._rewrite_value(inner)
            return out
        if isinstance(value, list):
            return [self._rewrite_value(item) for item in value]
        return value


def bind_session_scope(
    tool_name: str,
    args: Dict[str, Any],
    student_id: Optional[str],
) -> tuple[Dict[str, Any], bool, Optional[str]]:
    """
    Force session student_id into args for scoped tools.
    Returns (args, rewritten, deny_reason).
    """
    return ToolScopeNormalizer(
        tool_name,
        args,
        student_id=student_id,
        session_id=None,
    ).normalize()


def bind_session_id(
    tool_name: str,
    args: Dict[str, Any],
    session_id: Optional[str],
) -> Dict[str, Any]:
    """Inject session_id for conversation / session-scoped tools."""
    out = dict(args) if isinstance(args, dict) else {}
    if not session_id:
        return out
    if tool_name in ("db_query", "attachment_read") or "session" in str(out.get("query", "")).lower():
        if out.get("session_id") != session_id:
            out["session_id"] = session_id
    return out


def pre_tool_use(
    tool_name: str,
    args: Optional[Dict[str, Any]],
    ctx: GateContext,
) -> GateResult:
    raw = dict(args or {})

    # Visibility short-circuit
    visible = visible_tools_for(
        ctx.interaction_mode,
        ctx.subagent,
        plan_tool_allowlist=ctx.plan_tool_allowlist,
    )
    if ctx.interaction_mode == InteractionMode.ASK or tool_name not in visible:
        verdict = evaluate(
            tool_name,
            raw,
            interaction=ctx.interaction_mode,
            subagent=ctx.subagent,
            plan_tool_allowlist=ctx.plan_tool_allowlist,
        )
        if verdict.decision != Decision.ALLOW:
            ctx.denials.append({"tool": tool_name, "reason": verdict.reason})
            return GateResult(allowed=False, args=raw, verdict=verdict)

    # A scoped pipeline dispatcher is not a grant to administer the shared
    # graph. L1 still writes legacy JobPortrait/Skill/Person nodes without an
    # owner partition; neither a plan allowlist nor student_id rewriting makes
    # those writes safe. Confirmed personal graph publication has its own path.
    if tool_name == "pipeline_run" and raw.get("pipeline") == "L1_build_graph":
        reason = "Agent 不可执行管理性图谱构建；个人图谱须走已确认资料发布流程。"
        verdict = PermissionVerdict(Decision.DENY, reason=reason)
        ctx.denials.append({"tool": tool_name, "reason": reason})
        return GateResult(allowed=False, args=raw, verdict=verdict)

    bound, rewritten, deny_reason = ToolScopeNormalizer(
        tool_name,
        raw,
        student_id=ctx.student_id,
        session_id=ctx.session_id,
    ).normalize()
    if deny_reason:
        verdict = PermissionVerdict(Decision.DENY, reason=deny_reason)
        ctx.denials.append({"tool": tool_name, "reason": deny_reason})
        return GateResult(allowed=False, args=bound, verdict=verdict, rewritten=rewritten)

    verdict = evaluate(
        tool_name,
        bound,
        interaction=ctx.interaction_mode,
        subagent=ctx.subagent,
        plan_tool_allowlist=ctx.plan_tool_allowlist,
    )
    # Interactive ask → deny in HTTP (dontAsk semantics for ask decision)
    if verdict.decision == Decision.ASK:
        verdict = PermissionVerdict(
            Decision.DENY,
            reason=verdict.reason or "需要用户确认但当前为非交互会话，已拒绝。",
            pending_tool=verdict.pending_tool or {"tool_name": tool_name, "parameters": bound},
        )

    if verdict.decision != Decision.ALLOW:
        ctx.denials.append({"tool": tool_name, "reason": verdict.reason})
        return GateResult(allowed=False, args=bound, verdict=verdict, rewritten=rewritten)

    return GateResult(allowed=True, args=bound, verdict=verdict, rewritten=rewritten)
