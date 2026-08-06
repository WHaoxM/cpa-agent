"""
Dual-channel tool results: model_view (LLM) vs ui_view (API/UI).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional

from .field_policy import project_document_access_for_model, project_tool_result_text
from .tool_egress import public_tool_summary, sanitize_parameters

# Tools whose payload is a Document Access window — generic 4k cut is forbidden.
_DOCUMENT_ACCESS_TOOLS = frozenset({"attachment_read"})


def build_model_view(tool_name: str, raw_result: Any) -> str:
    text = raw_result if isinstance(raw_result, str) else str(raw_result)
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
    ok = status != "error" and "错误" not in text and "失败" not in text and "拒绝" not in text
    summary = public_tool_summary(tool_name, text, ok=ok)
    # Prefer field-policy short line for query dumps
    if "查询结果" in text:
        summary = project_tool_result_text(text, for_model=False)
    params = sanitize_parameters(parameters or {})
    m = re.search(r"task_[a-zA-Z0-9]+", text)
    if m:
        params["task_id"] = m.group(0)
    out = {
        "tool_name": tool_name,
        "status": "done" if ok else "error",
        "summary": summary,
        "result_preview": summary,
        "parameters": params,
        "channel": "ui",
    }
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
