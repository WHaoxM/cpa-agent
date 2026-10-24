"""Safe, versioned event projections for durable Agent tool calls.

The persisted event is intentionally a structural summary. Tool arguments,
tool output, prompts, and model reasoning never cross this projection.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, FrozenSet


TOOL_STARTED_EVENT = "run.tool_call_started"
TOOL_SETTLED_EVENT = "run.tool_call_settled"
_RUN_ID = re.compile(r"^run_[a-f0-9]{32}$")
_SAFE_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_CALL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_ARGUMENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SAFE_ERROR_CODES = frozenset({
    "permission_denied", "tool_timeout", "tool_connection_error",
    "tool_execution_error", "tool_output_truncated", "tool_result_missing",
    "invalid_tool_call_id", "tool_call_id_conflict",
})
_RESULT_STATUSES = frozenset({
    "done", "success", "completed", "partial", "error", "failed", "denied",
    "unavailable", "needs_input", "approval_required", "running", "pending",
    "queued",
})
_SETTLED_STATUSES = frozenset({
    "succeeded", "failed", "denied", "partial", "unavailable", "needs_input",
})
_PENDING_RESULT_STATUSES = frozenset({
    "needs_input", "approval_required", "partial", "unavailable", "running",
    "pending", "queued",
})
_MAX_OUTPUT_BYTES = 10 * 1024 * 1024


def safe_tool_error_code(error: Any) -> str:
    """Map internal errors to a small public-safe allowlist."""
    if error == "missing":
        return "tool_result_missing"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, TimeoutError):
        return "tool_timeout"
    if isinstance(error, ConnectionError):
        return "tool_connection_error"
    return "tool_execution_error"


def _stable_id(prefix: str, *parts: Any) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return prefix + hashlib.sha256(encoded).hexdigest()[:32]


def stable_turn_id(run_id: str, contract_revision: int) -> str:
    """A stable conversation turn identity across plan steps and retries."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid Agent run id for tool event")
    if type(contract_revision) is not int or contract_revision < 0:
        raise ValueError("invalid task contract revision for tool event")
    return _stable_id("turn_", run_id, contract_revision)


def stable_assistant_message_id(
    *,
    run_id: str,
    contract_revision: int,
    plan_version: int,
    step_index: int = 0,
    attempt: int = 0,
    model_iteration: int = 0,
    final: bool = False,
) -> str:
    """Generate stable internal message IDs without persisting model content."""
    turn_id = stable_turn_id(run_id, contract_revision)
    if final:
        parts = (turn_id, "final")
    else:
        if (type(plan_version) is not int or plan_version < 1
                or type(step_index) is not int or step_index < 1
                or type(attempt) is not int or attempt < 1
                or type(model_iteration) is not int or model_iteration < 1):
            raise ValueError("invalid assistant message coordinates")
        parts = (turn_id, plan_version, step_index, attempt, model_iteration)
    return _stable_id("msg_", *parts)


@dataclass(frozen=True)
class ToolEventContext:
    run_id: str
    student_id: str
    session_id: str
    claim_token: Optional[str]
    plan_version: int
    contract_revision: int
    step_index: int
    attempt: int
    allowed_tool_names: FrozenSet[str]

    def __post_init__(self) -> None:
        stable_turn_id(self.run_id, self.contract_revision)
        if not isinstance(self.student_id, str) or not self.student_id.strip():
            raise ValueError("tool event owner is required")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("tool event session is required")
        if self.claim_token is not None and (
            not isinstance(self.claim_token, str) or len(self.claim_token) != 32
        ):
            raise ValueError("tool event claim token is invalid")
        if (type(self.plan_version) is not int or self.plan_version < 1
                or type(self.step_index) is not int or self.step_index < 1
                or type(self.attempt) is not int or self.attempt < 1):
            raise ValueError("tool event step coordinates are invalid")
        if not isinstance(self.allowed_tool_names, frozenset) or not self.allowed_tool_names:
            raise ValueError("tool event allowlist is required")
        if any(not isinstance(name, str) or not _SAFE_TOOL_NAME.fullmatch(name)
               for name in self.allowed_tool_names):
            raise ValueError("tool event allowlist contains an invalid tool name")

    @property
    def turn_id(self) -> str:
        return stable_turn_id(self.run_id, self.contract_revision)


def _safe_argument_names(args: Any) -> list[str]:
    if not isinstance(args, Mapping):
        raise ValueError("tool call arguments must be an object")
    names = [key for key in args if isinstance(key, str) and _SAFE_ARGUMENT_NAME.fullmatch(key)]
    return sorted(set(names))[:32]


def build_tool_event_payload(
    context: ToolEventContext,
    *,
    event_type: str,
    assistant_message_id: str,
    tool_call_id: str,
    tool_name: str,
    args: Optional[Mapping[str, Any]] = None,
    tool_message_content: Any = None,
    tool_message_status: Optional[str] = None,
) -> dict[str, Any]:
    """Project a call into a typed event without retaining sensitive content."""
    if event_type not in {TOOL_STARTED_EVENT, TOOL_SETTLED_EVENT}:
        raise ValueError("unsupported tool event type")
    if tool_name not in context.allowed_tool_names or not _SAFE_TOOL_NAME.fullmatch(tool_name):
        raise ValueError("tool name is outside the durable run allowlist")
    if not isinstance(tool_call_id, str) or not _SAFE_CALL_ID.fullmatch(tool_call_id):
        raise ValueError("tool call id is invalid")
    if not isinstance(assistant_message_id, str) or not re.fullmatch(r"msg_[a-f0-9]{32}", assistant_message_id):
        raise ValueError("assistant message id is invalid")

    payload: dict[str, Any] = {
        "schema_version": 1,
        "turn_id": context.turn_id,
        "assistant_message_id": assistant_message_id,
        "tool_call_id": tool_call_id,
        "plan_version": context.plan_version,
        "contract_revision": context.contract_revision,
        "step_index": context.step_index,
        "attempt": context.attempt,
        "tool_name": tool_name,
    }
    if event_type == TOOL_STARTED_EVENT:
        argument_names = _safe_argument_names(args or {})
        payload.update({
            "status": "started",
            "input_summary": {
                "argument_count": len(args or {}),
                "argument_names": argument_names,
            },
        })
    else:
        status, output_summary = summarize_tool_result(
            tool_message_content, tool_message_status=tool_message_status,
        )
        payload.update({"status": status, "output_summary": output_summary})
    return validate_tool_event_payload(event_type, payload)


def summarize_tool_result(
    content: Any, *, tool_message_status: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Return only fixed status and structural metadata for a ToolMessage."""
    text = content if isinstance(content, str) else str(content or "")
    encoded_size = len(text.encode("utf-8", errors="replace"))
    bounded_size = min(encoded_size, _MAX_OUTPUT_BYTES)
    parsed: Any = None
    try:
        parsed = json.loads(text) if text else None
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        result_kind = "json_object"
        field_count = min(len(parsed), 256)
        raw_status = str(parsed.get("status") or "").lower()
        raw_error = parsed.get("error_code")
        attempts = parsed.get("attempts", 1)
        if type(attempts) is not int or attempts < 1:
            attempts = 1
        attempts = min(attempts, 4)
        success = parsed.get("success")
    elif isinstance(parsed, list):
        result_kind = "json_array"
        field_count = min(len(parsed), 256)
        raw_status, raw_error, attempts, success = "", None, 1, None
    elif text:
        result_kind = "text"
        field_count = 0
        raw_status, raw_error, attempts, success = "", None, 1, None
    else:
        result_kind = "empty"
        field_count = 0
        raw_status, raw_error, attempts, success = "", None, 1, None

    if raw_status not in _RESULT_STATUSES:
        raw_status = "unknown"
    if tool_message_status == "error" or raw_status in {"error", "failed"}:
        status = "failed"
    elif raw_status == "denied" or raw_error == "permission_denied":
        status = "denied"
    elif raw_status == "partial":
        status = "partial"
    elif raw_status == "unavailable":
        status = "unavailable"
    elif raw_status in {"needs_input", "approval_required"}:
        status = "needs_input"
    elif success is False and raw_status not in _PENDING_RESULT_STATUSES:
        status = "failed"
    else:
        status = "succeeded"

    error_code = raw_error if isinstance(raw_error, str) and raw_error in _SAFE_ERROR_CODES else None
    if status in {"failed", "denied"} and error_code is None:
        error_code = "tool_execution_error"
    return status, {
        "result_kind": result_kind,
        "output_bytes": bounded_size,
        "field_count": field_count,
        "attempts": attempts,
        "error_code": error_code,
    }


def validate_tool_event_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate persisted event shape and reject arbitrary payloads."""
    common = {
        "schema_version", "turn_id", "assistant_message_id", "tool_call_id",
        "plan_version", "contract_revision", "step_index", "attempt", "tool_name",
        "status",
    }
    if event_type == TOOL_STARTED_EVENT:
        allowed = common | {"input_summary"}
    elif event_type == TOOL_SETTLED_EVENT:
        allowed = common | {"output_summary"}
    else:
        raise ValueError("unsupported tool event type")
    if not isinstance(payload, Mapping) or set(payload) != allowed:
        raise ValueError("tool event payload has an invalid shape")
    if payload.get("schema_version") != 1:
        raise ValueError("tool event schema version is unsupported")
    if not isinstance(payload.get("turn_id"), str) or not re.fullmatch(r"turn_[a-f0-9]{32}", payload["turn_id"]):
        raise ValueError("tool event turn id is invalid")
    if not isinstance(payload.get("assistant_message_id"), str) or not re.fullmatch(r"msg_[a-f0-9]{32}", payload["assistant_message_id"]):
        raise ValueError("tool event assistant message id is invalid")
    if not isinstance(payload.get("tool_call_id"), str) or not _SAFE_CALL_ID.fullmatch(payload["tool_call_id"]):
        raise ValueError("tool event tool call id is invalid")
    if not isinstance(payload.get("tool_name"), str) or not _SAFE_TOOL_NAME.fullmatch(payload["tool_name"]):
        raise ValueError("tool event tool name is invalid")
    for field, minimum in (("plan_version", 1), ("contract_revision", 0), ("step_index", 1), ("attempt", 1)):
        value = payload.get(field)
        if type(value) is not int or value < minimum:
            raise ValueError(f"tool event {field} is invalid")

    if event_type == TOOL_STARTED_EVENT:
        if payload.get("status") != "started":
            raise ValueError("tool start event status is invalid")
        summary = payload.get("input_summary")
        if not isinstance(summary, Mapping) or set(summary) != {"argument_count", "argument_names"}:
            raise ValueError("tool input summary is invalid")
        count, names = summary.get("argument_count"), summary.get("argument_names")
        if type(count) is not int or not 0 <= count <= 256:
            raise ValueError("tool input argument count is invalid")
        if (not isinstance(names, list) or len(names) > 32
                or any(not isinstance(name, str) or not _SAFE_ARGUMENT_NAME.fullmatch(name) for name in names)
                or names != sorted(set(names)) or len(names) > count):
            raise ValueError("tool input argument names are invalid")
    else:
        if payload.get("status") not in _SETTLED_STATUSES:
            raise ValueError("tool settlement status is invalid")
        summary = payload.get("output_summary")
        if not isinstance(summary, Mapping) or set(summary) != {
            "result_kind", "output_bytes", "field_count", "attempts", "error_code",
        }:
            raise ValueError("tool output summary is invalid")
        if summary.get("result_kind") not in {"json_object", "json_array", "text", "empty"}:
            raise ValueError("tool result kind is invalid")
        for field, maximum in (("output_bytes", _MAX_OUTPUT_BYTES), ("field_count", 256), ("attempts", 4)):
            value = summary.get(field)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"tool output {field} is invalid")
        error_code = summary.get("error_code")
        if error_code is not None and error_code not in _SAFE_ERROR_CODES:
            raise ValueError("tool output error code is invalid")
    return dict(payload)
