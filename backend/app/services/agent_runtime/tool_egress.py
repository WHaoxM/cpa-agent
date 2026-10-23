"""
Tool egress boundary — what may leave the agent runtime toward API/UI.

Architecture rule: tool internals (raw SQL rows, PII, full JSON blobs, raw args)
must NOT be shipped to the client. The model may see richer ToolMessage content
in-process; the HTTP contract only gets redacted public cards.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .field_keys import NEVER_KEYS as _SENSITIVE_KEYS

# These values are injected from the authenticated request/session scope. They
# are useful inside the runtime and audit trail, but do not help users interpret
# a read-only tool card and can expose cross-service identifiers.
_OWNER_SCOPE_KEYS = frozenset({
    "student_id", "studentid", "user_id", "userid",
    "session_id", "sessionid", "conversation_id", "conversationid",
})

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ID_CARD_RE = re.compile(r"\b(\d{6})\d{8}(\d{3}[\dXx])\b")


def redact_text(text: str, *, max_len: Optional[int] = 160) -> str:
    if not text:
        return ""
    out = str(text)
    out = _PHONE_RE.sub(r"\1****\2", out)
    out = _EMAIL_RE.sub("[email]", out)
    out = _ID_CARD_RE.sub(r"\1********\2", out)
    # collapse huge json dumps
    if "profile_json=" in out or "posting_json=" in out:
        out = re.sub(r"profile_json=\{[^}]{0,2000}", "profile_json=[redacted]", out)
        out = re.sub(r"posting_json=\{[^}]{0,2000}", "posting_json=[redacted]", out)
    if max_len is not None and len(out) > max_len:
        out = out[: max_len - 1] + "…"
    return out


def sanitize_parameters(params: Any) -> Dict[str, str]:
    """Public-safe arg digest for UI (owner scope IDs, secrets/blobs excluded)."""
    if not isinstance(params, dict):
        return {}
    safe: Dict[str, str] = {}
    for key, val in params.items():
        lk = str(key).lower()
        if (
            lk in _SENSITIVE_KEYS
            or lk in _OWNER_SCOPE_KEYS
            or lk.endswith("_json")
            or lk.endswith("_token")
        ):
            continue
        s = str(val) if val is not None else ""
        if len(s) > 80:
            s = s[:77] + "…"
        safe[str(key)] = redact_text(s, max_len=80)
    return safe


def public_tool_summary(tool_name: str, result_text: str = "", ok: bool = True, *, status: Optional[str] = None) -> str:
    """Render producer state; result prose cannot prove business completion."""
    labels = {"done": "调用已结束", "completed": "调用已结束", "error": "未成功",
              "failed": "未成功", "partial": "部分完成", "needs_input": "待补充或确认",
              "running": "进行中", "pending": "等待执行", "queued": "等待执行",
              "unverified": "结果待核验", "cancelled": "已取消",
              "unavailable": "资料不可用"}
    state = status or ("done" if ok else "error")
    return f"{tool_name or 'tool'} {labels.get(state, '结果待核验')}（已脱敏）"


def sanitize_tool_call_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert an internal tool_call dict into a client-safe card.
    Drops raw result bodies and sensitive parameters.
    """
    name = str(
        record.get("tool_name")
        or record.get("name")
        or record.get("tool")
        or "tool"
    )
    raw_preview = str(
        record.get("result_preview")
        or record.get("summary")
        or record.get("result")
        or ""
    )
    params = record.get("parameters") or record.get("args") or {}
    status = str(record.get("status") or "unverified")
    summary = public_tool_summary(name, status=status)
    row_count = record.get("row_count")
    if isinstance(row_count, int) and not isinstance(row_count, bool) and row_count >= 0:
        summary += f"，返回 {row_count} 条"
    out: Dict[str, Any] = {
        "tool_name": name,
        "status": status,
        "summary": summary,
        "result_preview": summary,  # never raw DB dump
        "parameters": sanitize_parameters(params),
        "egress": "redacted",
    }
    if status in {"error", "failed", "denied", "unavailable"}:
        error_code = record.get("error_code")
        if isinstance(error_code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
            out["error_code"] = error_code
        if type(record.get("retryable")) is bool:
            out["retryable"] = record["retryable"]
    attempts = record.get("attempts")
    if type(attempts) is int and 1 <= attempts <= 9:
        out["attempts"] = attempts
    if isinstance(row_count, int) and not isinstance(row_count, bool) and row_count >= 0:
        out["row_count"] = row_count
    if record.get("truncated") is True:
        out["truncated"] = True
        continuation = record.get("continuation")
        # build_ui_view emits this fixed pagination form from a validated
        # integer next_offset. Never forward arbitrary tool-result prose.
        out["continuation"] = (
            continuation
            if isinstance(continuation, str)
            and re.fullmatch(r"下一页从 offset=\d{1,9} 继续读取", continuation)
            else "请缩小查询范围后重试"
        )
    # Keep task_id if present for pipeline polling
    if isinstance(params, dict):
        tid = params.get("task_id") or params.get("taskId")
        if tid:
            out["parameters"]["task_id"] = str(tid)
    refs = record.get("artifact_refs") or record.get("artifacts")
    if isinstance(refs, list):
        safe_refs = []
        for ref in refs[:6]:
            if not isinstance(ref, dict):
                continue
            safe_refs.append({
                "artifact_id": str(ref.get("artifact_id") or ""),
                "kind": str(ref.get("kind") or "file"),
                "status": str(ref.get("status") or "unverified"),
                "title": redact_text(str(ref.get("title") or "成果物"), max_len=120),
                "summary": redact_text(str(ref.get("summary") or ""), max_len=240),
                "preview": ref.get("preview") if isinstance(ref.get("preview"), dict) else {},
                "actions": ref.get("actions") if isinstance(ref.get("actions"), list) else [],
                "version": int(ref.get("version") or 1),
                "created_at": str(ref.get("created_at") or ""),
            })
        if safe_refs:
            out["artifact_refs"] = safe_refs
    return out


def sanitize_tool_calls(records: Optional[List[Any]]) -> List[Dict[str, Any]]:
    if not records:
        return []
    out = []
    for r in records:
        if isinstance(r, dict):
            out.append(sanitize_tool_call_record(r))
    return out
