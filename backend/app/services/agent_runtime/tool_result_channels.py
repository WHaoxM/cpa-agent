"""
Dual-channel tool results: model_view (LLM) vs ui_view (API/UI).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from .field_policy import project_document_access_for_model, project_tool_result_text
from .tool_egress import public_tool_summary, sanitize_parameters

# Tools whose payload is a Document Access window — generic 4k cut is forbidden.
_DOCUMENT_ACCESS_TOOLS = frozenset({"attachment_read"})


def build_model_view(tool_name: str, raw_result: Any) -> str:
    text = raw_result if isinstance(raw_result, str) else (
        json.dumps(raw_result, ensure_ascii=False) if isinstance(raw_result, dict) else str(raw_result)
    )
    if tool_name == "graph_query":
        payload = _maybe_json_payload(raw_result)
        if isinstance(payload.get("message"), str) and not (
            payload.get("success") is False
            or str(payload.get("status") or "").lower() in {"error", "failed", "denied", "unavailable"}
        ):
            text = payload["message"]
            if payload.get("truncated"):
                text += "\n[partial] " + str(payload.get("continuation") or "请缩小查询范围后重试")
    if tool_name in _DOCUMENT_ACCESS_TOOLS:
        return project_document_access_for_model(text)
    return project_tool_result_text(text, for_model=True)


def build_ui_view(
    tool_name: str,
    raw_result: Any,
    *,
    parameters: Optional[Dict[str, Any]] = None,
    status: str = "done",
) -> Dict[str, Any]:
    payload = _maybe_json_payload(raw_result)
    text = _display_text(raw_result, payload)
    result_status = str(payload.get("status") or "").lower()
    explicit_failed = payload.get("success") is False or result_status in {"failed", "error", "denied", "unavailable"}
    needs_input = result_status in {"needs_input", "approval_required"}
    # Legacy free text cannot attest business success/failure. Surface unknown
    # outcome as partial until that producer adopts the structured contract.
    partial = result_status == "partial" or payload.get("verified") is False or not payload
    if status == "error":
        ui_status = "error"
    elif needs_input:
        ui_status = "needs_input"
    elif result_status in {"running", "pending", "queued", "cancelled"}:
        ui_status = result_status
    elif result_status == "partial":
        ui_status = "partial"
    elif explicit_failed:
        ui_status = "error"
    elif partial:
        ui_status = "partial"
    elif payload.get("success") is True or result_status in {"done", "completed", "ready", "empty"}:
        ui_status = "done"
    else:
        ui_status = "unverified"
    summary = public_tool_summary(tool_name, status=ui_status)
    row_count = payload.get("row_count")
    if isinstance(row_count, int) and not isinstance(row_count, bool) and row_count >= 0:
        summary += f"，返回 {row_count} 条"
    params = sanitize_parameters(parameters or {})
    # A task-shaped substring is not a task reference. pipeline_run creates its
    # task server-side; its structured ID remains subject to business readback.
    if tool_name == "pipeline_run" and isinstance(payload.get("task_id"), str):
        params["task_id"] = payload["task_id"]
    out = {
        "tool_name": tool_name,
        "status": ui_status,
        "summary": summary,
        "result_preview": summary,
        "parameters": params,
        "channel": "ui",
    }
    if isinstance(payload.get("error_code"), str) and ui_status == "error":
        out["error_code"] = payload["error_code"]
        out["retryable"] = payload.get("retryable") is True
    if type(payload.get("attempts")) is int and payload["attempts"] > 0:
        out["attempts"] = payload["attempts"]
    if isinstance(row_count, int) and not isinstance(row_count, bool) and row_count >= 0:
        out["row_count"] = row_count
    if payload.get("truncated") is True:
        out["truncated"] = True
        next_offset = payload.get("next_offset")
        out["continuation"] = (
            f"下一页从 offset={next_offset} 继续读取"
            if payload.get("has_more") is True
            and type(next_offset) is int and next_offset >= 0
            else "请缩小查询范围后重试"
        )
    candidates = payload.get("artifact_candidates") if isinstance(payload, dict) else None
    if isinstance(candidates, list):
        out["artifact_candidates"] = candidates[:8]
    return out


def digest_text(text: str, n: int = 32) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()[:n]


def _maybe_json_payload(raw_result: Any) -> Dict[str, Any]:
    if isinstance(raw_result, dict):
        return raw_result
    if not isinstance(raw_result, str):
        return {}
    text = raw_result.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _display_text(raw_result: Any, payload: Dict[str, Any]) -> str:
    if payload:
        for key in ("message", "summary", "result_preview"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return raw_result if isinstance(raw_result, str) else str(raw_result)
