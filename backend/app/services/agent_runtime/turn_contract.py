"""Typed public turn contracts shared by HTTP, SSE and history replay."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from .reply_egress import sanitize_user_reply

MESSAGE_PART_TYPES = {
    "text",
    "progress",
    "tool_call",
    "attachment",
    "artifact_ref",
    "citation",
    "data_card",
    "upgrade_hint",
    "notice",
}

ARTIFACT_KINDS = {
    "portrait",
    "match",
    "career_report",
    "action_plan",
    "file",
    "pipeline_task",
}

TURN_STATUSES = frozenset({"completed", "needs_input", "running", "partial", "failed"})
TURN_REASONS = frozenset({
    "turn_finished", "clarification_required", "run_active", "untracked_running",
    "execution_failed", "document_incomplete", "input_or_permission_incomplete",
    "runtime_failed", "runtime_partial", "artifact_still_active", "precondition_blocked",
    "tool_partial", "tool_input_required",
    "context_truncated",
})
_FAILED_TOOL_STATUSES = frozenset({"failed", "error", "denied", "unavailable"})
_PARTIAL_TOOL_STATUSES = frozenset({"partial", "needs_input", "approval_required"})
_ACTIVE_ARTIFACT_STATUSES = frozenset({"queued", "pending", "running", "processing"})
_CONTEXT_SLOTS = frozenset({"summary", "memory_digest", "attachment_context"})


def derive_turn_outcome(raw: Any) -> Dict[str, Any]:
    """Describe the *turn*, never infer that a domain artifact was persisted.

    Only concrete runtime flags may lower completion. A pending long-running run
    must provide a server run ID; fluent reply text and fork-job notifications
    are not proof of completion or recoverability.
    """
    data = raw if isinstance(raw, dict) else {}
    orch = data.get("orchestration") if isinstance(data.get("orchestration"), dict) else {}
    view = orch.get("supervisor_view") if isinstance(orch.get("supervisor_view"), dict) else {}
    doc_pack = view.get("doc_pack") if isinstance(view.get("doc_pack"), dict) else {}
    explicit = str(data.get("turn_status") or "").strip().lower()
    run_id = _safe_run_id(data.get("run_id"))
    reply = str(data.get("reply") or "").strip()
    calls = data.get("tool_calls") if isinstance(data.get("tool_calls"), list) else []
    failed_tool = any(_tool_failed(call) for call in calls)
    needs_input_tool = any(
        isinstance(call, dict) and str(call.get("status") or "").lower() in {"needs_input", "approval_required"}
        for call in calls
    )
    partial_tool = any(
        isinstance(call, dict) and str(call.get("status") or "").lower() in _PARTIAL_TOOL_STATUSES
        for call in calls
    )
    plan = orch.get("plan_results") if isinstance(orch.get("plan_results"), list) else []
    failed_plan = any(isinstance(step, dict) and step.get("ok") is False for step in plan)
    denials = data.get("permission_denials") or orch.get("permission_denials") or []
    mount_errors = orch.get("mount_errors") or []
    context_truncation = data.get("context_truncation")
    context_slots = sorted({
        slot for slot in (context_truncation.get("slots") or [])
        if isinstance(slot, str) and slot in _CONTEXT_SLOTS
    }) if isinstance(context_truncation, dict) else []

    if failed_tool or failed_plan:
        status, reason = ("partial" if reply else "failed"), "execution_failed"
    elif needs_input_tool:
        status, reason = "needs_input", "tool_input_required"
    elif partial_tool:
        status, reason = "partial", "tool_partial"
    elif doc_pack.get("all_complete") is False:
        status, reason = "partial", "document_incomplete"
    elif context_slots:
        status, reason = "partial", "context_truncated"
    elif denials or mount_errors or data.get("incomplete") is True:
        status, reason = "partial", "input_or_permission_incomplete"
    elif explicit == "failed":
        status, reason = "failed", "runtime_failed"
    elif explicit == "partial":
        status, reason = "partial", "runtime_partial"
    elif explicit == "running":
        status, reason = ("running", "run_active") if run_id else ("partial", "untracked_running")
    elif explicit == "needs_input" or view.get("lead_action") == "clarify" or orch.get("strategy") == "agent_clarify":
        status, reason = "needs_input", "clarification_required"
    elif orch.get("strategy") == "gate_block":
        status, reason = "partial", "precondition_blocked"
    elif any(
        isinstance(ref, dict) and str(ref.get("status") or "").lower() in _ACTIVE_ARTIFACT_STATUSES
        for call in calls if isinstance(call, dict)
        for ref in (call.get("artifact_refs") or []) if isinstance(call.get("artifact_refs"), list)
    ):
        status, reason = "partial", "artifact_still_active"
    else:
        status, reason = "completed", "turn_finished"

    next_action = {
        "completed": "none",
        "needs_input": "answer_in_session",
        "running": "poll_run",
        "partial": "retry_new_turn",
        "failed": "retry_new_turn",
    }[status]
    if status == "partial" and reason == "context_truncated":
        # No generic continuation cursor exists for these injected slots.
        # Ask for the missing detail rather than claiming an automatic resume.
        next_action = "answer_in_session"
    outcome: Dict[str, Any] = {
        "status": status,
        "completion_scope": "turn",
        "reason": reason,
        "next_action": next_action,
        # A chat session is not a persisted LangGraph interrupt/checkpoint.
        "resume_state": "not_available",
    }
    if status == "running" and run_id:
        outcome["run_id"] = run_id
    if context_slots:
        outcome["context_slots"] = context_slots
    return outcome


def normalize_turn_outcome(raw: Any) -> Optional[Dict[str, Any]]:
    """Whitelist persisted outcome fields before exposing session history."""
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "")
    if status not in TURN_STATUSES:
        return None
    run_id = _safe_run_id(raw.get("run_id"))
    if status == "running" and not run_id:
        status = "partial"
    next_action = {
        "completed": "none",
        "needs_input": "answer_in_session",
        "running": "poll_run",
        "partial": "retry_new_turn",
        "failed": "retry_new_turn",
    }[status]
    raw_reason = raw.get("reason")
    reason = raw_reason if isinstance(raw_reason, str) and raw_reason in TURN_REASONS else "unspecified"
    result = {
        "status": status,
        "completion_scope": "turn",
        "reason": reason,
        "next_action": next_action,
        "resume_state": "not_available",
    }
    if reason == "context_truncated":
        result["next_action"] = "answer_in_session"
        slots = raw.get("context_slots")
        if isinstance(slots, list):
            result["context_slots"] = sorted({
                slot for slot in slots
                if isinstance(slot, str) and slot in _CONTEXT_SLOTS
            })
    if status == "running" and run_id:
        result["run_id"] = run_id
    return result


def _safe_run_id(raw: Any) -> Optional[str]:
    if not isinstance(raw, str) or not 1 <= len(raw) <= 96:
        return None
    if not all(c.isascii() and (c.isalnum() or c in "_-") for c in raw):
        return None
    return raw


def _tool_failed(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("success") is False or str(raw.get("status") or "").lower() in _FAILED_TOOL_STATUSES:
        return True
    result = raw.get("result")
    return isinstance(result, dict) and result.get("success") is False


def new_turn_id() -> str:
    return f"turn_{uuid.uuid4().hex[:16]}"


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def normalize_artifact_ref(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()
    if kind not in ARTIFACT_KINDS:
        kind = "file"
    artifact_id = str(raw.get("artifact_id") or "").strip()
    if not artifact_id:
        return None
    status = str(raw.get("status") or "unverified").strip() or "unverified"
    title = sanitize_user_reply(str(raw.get("title") or _default_artifact_title(kind)), max_len=120)
    summary = sanitize_user_reply(str(raw.get("summary") or ""), max_len=240)
    preview = _typed_preview(raw.get("preview"), kind)
    actions = raw.get("actions") if isinstance(raw.get("actions"), list) else []
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "status": status,
        "title": title,
        "summary": summary,
        "preview": preview,
        "actions": _safe_actions(actions),
        "version": int(raw.get("version") or 1),
        "created_at": str(raw.get("created_at") or utc_now_iso()),
    }


def extract_artifact_refs(tool_calls: Any) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if not isinstance(tool_calls, list):
        return refs
    seen = set()
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        raw_refs = call.get("artifact_refs") or call.get("artifacts") or []
        if not isinstance(raw_refs, list):
            continue
        for raw in raw_refs:
            ref = normalize_artifact_ref(raw)
            if not ref or ref["artifact_id"] in seen:
                continue
            seen.add(ref["artifact_id"])
            refs.append(ref)
    return refs


def build_message_parts(
    *,
    reply: str = "",
    tool_calls: Any = None,
    orchestration: Optional[Dict[str, Any]] = None,
    upgrade_hint: Optional[Dict[str, Any]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    permission_denials: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []
    orch = orchestration if isinstance(orchestration, dict) else {}
    progress = orch.get("progress") if isinstance(orch.get("progress"), dict) else {}
    if progress:
        parts.append({
            "type": "progress",
            "status": "done",
            "title": _progress_title(progress),
            "summary": _progress_summary(progress),
            "progress": progress,
        })

    for idx, call in enumerate(tool_calls if isinstance(tool_calls, list) else []):
        if not isinstance(call, dict):
            continue
        parts.append({
            "type": "tool_call",
            "tool_call": call,
            "status": str(call.get("status") or "done"),
            "title": sanitize_user_reply(str(call.get("summary") or call.get("tool_name") or f"工具 {idx + 1}"), max_len=120),
        })

    for ref in artifacts or []:
        normalized = normalize_artifact_ref(ref)
        if normalized:
            parts.append({"type": "artifact_ref", "artifact": normalized})

    for citation in (orch.get("citations") if isinstance(orch, dict) else []) or []:
        if isinstance(citation, dict):
            parts.append(_normalize_citation(citation))

    for card in (orch.get("data_cards") if isinstance(orch, dict) else []) or []:
        if isinstance(card, dict):
            parts.append(_normalize_data_card(card))

    for denial in permission_denials or []:
        if not isinstance(denial, dict):
            continue
        msg = denial.get("message") or denial.get("reason") or "当前模式未允许这项操作。"
        parts.append({
            "type": "notice",
            "level": "warning",
            "text": sanitize_user_reply(str(msg), max_len=240),
        })

    if reply:
        parts.append({
            "type": "text",
            "text": sanitize_user_reply(str(reply), max_len=48000),
        })

    if isinstance(upgrade_hint, dict) and upgrade_hint.get("to"):
        parts.append({
            "type": "upgrade_hint",
            "to": str(upgrade_hint.get("to") or ""),
            "reason": sanitize_user_reply(str(upgrade_hint.get("reason") or ""), max_len=200),
        })
    return parts


def normalize_message_parts(parts: Any, *, fallback_text: str = "") -> List[Dict[str, Any]]:
    if isinstance(parts, list):
        out = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            ptype = str(part.get("type") or "")
            if ptype not in MESSAGE_PART_TYPES:
                continue
            # Explicit parts and persisted parts_v1 must cross the same public
            # citation boundary as citations generated from orchestration.
            out.append(_normalize_citation(part) if ptype == "citation" else part)
        if out:
            return out
    if fallback_text:
        return [{"type": "text", "text": sanitize_user_reply(str(fallback_text), max_len=48000)}]
    return []


def build_turn_events(
    *,
    turn_id: str,
    session_id: Optional[str],
    parts: Iterable[Dict[str, Any]],
    reply: str,
    orchestration: Optional[Dict[str, Any]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    outcome: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    def add(event_type: str, payload: Dict[str, Any]) -> None:
        events.append({
            "event_id": len(events) + 1,
            "turn_id": turn_id,
            "type": event_type,
            **payload,
        })

    add("turn_start", {"session_id": session_id})
    orch = orchestration if isinstance(orchestration, dict) else {}
    progress = orch.get("progress") if isinstance(orch.get("progress"), dict) else {}
    if progress:
        add("progress", {"progress": progress})

    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "tool_call":
            call = part.get("tool_call") if isinstance(part.get("tool_call"), dict) else {}
            add("tool_done", {"tool_call": call})
        elif part.get("type") == "citation":
            add("citation", {"citation": _normalize_citation(part)})

    for ref in artifacts or []:
        normalized = normalize_artifact_ref(ref)
        if normalized:
            add("artifact_committed", {"artifact": normalized})

    text = str(reply or "")
    if text:
        chunk_size = 120
        for i in range(0, len(text), chunk_size):
            add("reply_delta", {"text": text[i:i + chunk_size]})
    add("turn_done", {"session_id": session_id, "outcome": normalize_turn_outcome(outcome)})
    return events


def _safe_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in preview.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            out[str(key)] = sanitize_user_reply(str(value), max_len=160) if isinstance(value, str) else value
        elif isinstance(value, list):
            out[str(key)] = [
                sanitize_user_reply(str(v), max_len=80)
                for v in value[:8]
                if isinstance(v, (str, int, float, bool))
            ]
    return out


def _typed_preview(raw: Any, kind: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    if kind == "portrait":
        return _preview_portrait(raw)
    if kind == "match":
        return _preview_match(raw)
    if kind == "career_report":
        return _preview_report(raw)
    if kind == "action_plan":
        return _preview_plan(raw)
    return _safe_preview(raw)


def _preview_portrait(raw: Dict[str, Any]) -> Dict[str, Any]:
    scores = raw.get("scores")
    if not isinstance(scores, dict):
        scores = {}
    out: Dict[str, Any] = {"type": "portrait_preview"}
    safe_scores = {}
    for dim in ("d1", "d2", "d3", "d4"):
        val = scores.get(dim)
        if isinstance(val, (int, float)):
            safe_scores[dim] = round(float(val), 2)
    if safe_scores:
        out["scores"] = safe_scores
    strength = raw.get("strengths")
    if isinstance(strength, list):
        out["strengths"] = [sanitize_user_reply(str(s), max_len=40) for s in strength[:4] if isinstance(s, str)]
    weakness = raw.get("weaknesses")
    if isinstance(weakness, list):
        out["weaknesses"] = [sanitize_user_reply(str(w), max_len=40) for w in weakness[:4] if isinstance(w, str)]
    return out


def _preview_match(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "match_preview"}
    total = raw.get("total_score")
    if isinstance(total, (int, float)):
        out["total_score"] = round(float(total), 2)
    coverage = raw.get("coverage")
    if isinstance(coverage, (int, float)):
        out["coverage"] = round(float(coverage), 2)
    gaps = raw.get("gaps")
    if isinstance(gaps, list):
        out["gaps"] = [sanitize_user_reply(str(g), max_len=40) for g in gaps[:5] if isinstance(g, str)]
    hard_pass = raw.get("hard_pass")
    if isinstance(hard_pass, bool):
        out["hard_pass"] = hard_pass
    return out


def _preview_report(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "report_preview"}
    chapters = raw.get("chapters")
    if isinstance(chapters, list):
        out["chapters"] = [sanitize_user_reply(str(c), max_len=40) for c in chapters[:8] if isinstance(c, str)]
    word_count = raw.get("word_count")
    if isinstance(word_count, (int, float)):
        out["word_count"] = int(word_count)
    return out


def _preview_plan(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "plan_preview"}
    steps = raw.get("steps")
    if isinstance(steps, (int, float)):
        out["steps"] = int(steps)
    completed = raw.get("completed")
    if isinstance(completed, (int, float)):
        out["completed"] = int(completed)
    duration = raw.get("duration")
    if isinstance(duration, str):
        out["duration"] = sanitize_user_reply(duration, max_len=40)
    return out


def _safe_actions(actions: List[Any]) -> List[Dict[str, str]]:
    allowed = {
        "view_portrait",
        "open_match",
        "open_report",
        "continue_plan",
        "open_file",
        "open_pipeline_task",
        "confirm_resume",
    }
    out: List[Dict[str, str]] = []
    for action in actions[:4]:
        if not isinstance(action, dict):
            continue
        action_id = str(action.get("id") or action.get("action") or "")
        if action_id not in allowed:
            continue
        out.append({
            "id": action_id,
            "label": sanitize_user_reply(str(action.get("label") or action_id), max_len=40),
        })
    return out


def _default_artifact_title(kind: str) -> str:
    return {
        "portrait": "能力画像",
        "match": "人岗匹配",
        "career_report": "职业报告",
        "action_plan": "行动计划",
        "pipeline_task": "管线任务",
        "file": "文件产物",
    }.get(kind, "成果物")


def _progress_title(progress: Dict[str, Any]) -> str:
    if progress.get("lead_action"):
        return f"当前阶段：{progress.get('lead_action')}"
    if progress.get("path"):
        return f"执行路径：{progress.get('path')}"
    return "本轮进度"


def _progress_summary(progress: Dict[str, Any]) -> str:
    plan = progress.get("plan_sketch")
    if isinstance(plan, list) and plan:
        return " → ".join(str(x) for x in plan[:4])
    missing = progress.get("missing_slots")
    if isinstance(missing, list) and missing:
        return "还需要补充：" + "、".join(str(item) for item in missing[:4])
    if "document_complete" in progress:
        return "附件已完整读取" if progress.get("document_complete") else "附件仅部分可用"
    return ""


def _normalize_citation(raw: Dict[str, Any]) -> Dict[str, Any]:
    source = str(raw.get("source") or raw.get("label") or "").strip()
    if not source:
        source = "未知来源"
    quote = sanitize_user_reply(str(raw.get("quote") or raw.get("snippet") or ""), max_len=500)
    page = raw.get("page")
    section = str(raw.get("section") or "").strip() or None
    url = _safe_citation_url(raw.get("url"))
    relevance = raw.get("relevance")
    return {
        "type": "citation",
        "source": sanitize_user_reply(source, max_len=120),
        "quote": quote,
        "page": int(page) if isinstance(page, (int, float)) else None,
        "section": sanitize_user_reply(str(section), max_len=80) if section else None,
        "url": url,
        "relevance": float(relevance) if isinstance(relevance, (int, float)) else None,
    }


def _safe_citation_url(raw: Any) -> Optional[str]:
    """Only browser-safe absolute web links may reach the public href field."""
    if not isinstance(raw, str) or not raw or len(raw) > 2048:
        return None
    # urlsplit strips some C0 controls; reject them before parsing instead of
    # accepting a URL the browser may normalize differently.
    if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in raw):
        return None
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        _ = parsed.port  # Reject malformed explicit ports.
    except ValueError:
        return None
    return raw


def _normalize_data_card(raw: Dict[str, Any]) -> Dict[str, Any]:
    title = sanitize_user_reply(str(raw.get("title") or ""), max_len=80)
    card_type = str(raw.get("card_type") or "kv").strip()
    if card_type not in {"kv", "score", "bar", "list", "comparison"}:
        card_type = "kv"
    items = raw.get("items")
    if not isinstance(items, list):
        items = []
    safe_items: List[Dict[str, Any]] = []
    for item in items[:12]:
        if not isinstance(item, dict):
            continue
        safe_items.append({
            "label": sanitize_user_reply(str(item.get("label") or ""), max_len=60),
            "value": _sanitize_card_value(item.get("value")),
        })
    return {
        "type": "data_card",
        "title": title,
        "card_type": card_type,
        "items": safe_items,
    }


def _sanitize_card_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return sanitize_user_reply(str(value), max_len=200) if isinstance(value, str) else value
    if isinstance(value, list):
        return [
            sanitize_user_reply(str(v), max_len=80)
            for v in value[:8]
            if isinstance(v, (str, int, float, bool))
        ]
    return str(value)[:200]
