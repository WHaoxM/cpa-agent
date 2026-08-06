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

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ID_CARD_RE = re.compile(r"\b(\d{6})\d{8}(\d{3}[\dXx])\b")


def redact_text(text: str, *, max_len: int = 160) -> str:
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
    if len(out) > max_len:
        out = out[: max_len - 1] + "…"
    return out


def sanitize_parameters(params: Any) -> Dict[str, str]:
    """Public-safe arg digest for UI (ids ok, secrets/blobs no)."""
    if not isinstance(params, dict):
        return {}
    safe: Dict[str, str] = {}
    for key, val in params.items():
        lk = str(key).lower()
        if lk in _SENSITIVE_KEYS or lk.endswith("_json") or lk.endswith("_token"):
            continue
        s = str(val) if val is not None else ""
        if len(s) > 80:
            s = s[:77] + "…"
        safe[str(key)] = redact_text(s, max_len=80)
    return safe


def public_tool_summary(tool_name: str, result_text: str = "", ok: bool = True) -> str:
    name = tool_name or "tool"
    if not ok or (result_text and ("错误" in result_text or "失败" in result_text)):
        snippet = redact_text(result_text, max_len=100)
        return f"{name} 未成功" + (f"：{snippet}" if snippet else "")
    # Prefer status line over raw dump
    if "任务ID" in (result_text or "") or "task_" in (result_text or ""):
        m = re.search(r"task_[a-zA-Z0-9]+", result_text or "")
        tid = m.group(0) if m else ""
        return f"{name} 已完成" + (f"（{tid}）" if tid else "")
    if "查询结果" in (result_text or ""):
        m = re.search(r"查询结果\s*\((\d+)\s*条\)", result_text or "")
        n = m.group(1) if m else "?"
        return f"{name} 完成，返回 {n} 条（已脱敏）"
    if "skills_count" in (result_text or ""):
        return f"{name} 完成"
    return f"{name} 已完成"


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
    summary = public_tool_summary(name, raw_preview, ok=True)
    out: Dict[str, Any] = {
        "tool_name": name,
        "status": str(record.get("status") or "done"),
        "summary": summary,
        "result_preview": summary,  # never raw DB dump
        "parameters": sanitize_parameters(params),
        "egress": "redacted",
    }
    # Keep task_id if present for pipeline polling
    if isinstance(params, dict):
        tid = params.get("task_id") or params.get("taskId")
        if tid:
            out["parameters"]["task_id"] = str(tid)
    m = re.search(r"task_[a-zA-Z0-9]+", raw_preview)
    if m and "task_id" not in out["parameters"]:
        out["parameters"]["task_id"] = m.group(0)
    refs = record.get("artifact_refs") or record.get("artifacts")
    if isinstance(refs, list):
        safe_refs = []
        for ref in refs[:6]:
            if not isinstance(ref, dict):
                continue
            safe_refs.append({
                "artifact_id": str(ref.get("artifact_id") or ""),
                "kind": str(ref.get("kind") or "file"),
                "status": str(ref.get("status") or "completed"),
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
