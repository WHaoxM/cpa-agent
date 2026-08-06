"""Typed public turn contracts shared by HTTP, SSE and history replay."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .reply_egress import sanitize_user_reply

MESSAGE_PART_TYPES = {
    "text",
    "progress",
    "tool_call",
    "attachment",
    "artifact_ref",
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
    status = str(raw.get("status") or "completed").strip() or "completed"
    title = sanitize_user_reply(str(raw.get("title") or _default_artifact_title(kind)), max_len=120)
    summary = sanitize_user_reply(str(raw.get("summary") or ""), max_len=240)
    preview = raw.get("preview") if isinstance(raw.get("preview"), dict) else {}
    actions = raw.get("actions") if isinstance(raw.get("actions"), list) else []
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "status": status,
        "title": title,
        "summary": summary,
        "preview": _safe_preview(preview),
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
            out.append(part)
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

    for ref in artifacts or []:
        normalized = normalize_artifact_ref(ref)
        if normalized:
            add("artifact_committed", {"artifact": normalized})

    text = str(reply or "")
    if text:
        chunk_size = 120
        for i in range(0, len(text), chunk_size):
            add("reply_delta", {"text": text[i:i + chunk_size]})
    add("turn_done", {"session_id": session_id})
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


def _safe_actions(actions: List[Any]) -> List[Dict[str, str]]:
    allowed = {
        "view_portrait",
        "open_match",
        "open_report",
        "continue_plan",
        "open_file",
        "open_pipeline_task",
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
        return "还需要补充：" + "、".join(str(x) for x in missing[:4])
    if "document_complete" in progress:
        return "附件已完整读取" if progress.get("document_complete") else "附件仅部分可用"
    return ""
