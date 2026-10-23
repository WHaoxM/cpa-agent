"""Single public egress projection for Agent HTTP/SSE/session views."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .field_keys import NEVER_KEYS
from .reply_egress import sanitize_user_reply
from .thinking_egress import sanitize_thinking_steps
from .tool_egress import sanitize_tool_calls
from .turn_contract import (
    build_message_parts,
    build_turn_events,
    derive_turn_outcome,
    extract_artifact_refs,
    new_turn_id,
    normalize_artifact_ref,
    normalize_message_parts,
    normalize_turn_outcome,
)


_PUBLIC_EVIDENCE_STATUSES = frozenset({"completed", "partial", "unavailable"})
_PUBLIC_EVIDENCE_COVERAGE_REASONS = frozenset({
    "not_read", "char_budget", "budget_too_small", "reference_not_current",
    "reference_version_conflict", "continuation_invalid", "evidence_not_needed",
    "selection_unavailable", "no_valid_selection", "no_valid_reference",
    "stale_report_reference",
    "item_limit", "source_unavailable", "complete",
})


@dataclass(frozen=True)
class EgressContext:
    engine: str = "supervisor"
    interaction_mode: str = "ask"


@dataclass(frozen=True)
class EgressEnvelope:
    result: Dict[str, Any]
    context: EgressContext = field(default_factory=EgressContext)


@dataclass(frozen=True)
class PublicTurn:
    turn_id: str
    outcome: Dict[str, Any]
    reply: str
    tool_calls: List[Dict[str, Any]]
    thinking_steps: List[str]
    parts: List[Dict[str, Any]]
    artifacts: List[Dict[str, Any]]
    events: List[Dict[str, Any]]
    session_id: Optional[str]
    elapsed_seconds: float
    active_subagent: str
    engine: str
    interaction_mode: str
    upgrade_hint: Optional[Dict[str, Any]]
    fork_jobs_enqueued: List[str]
    permission_denials: List[Dict[str, str]]
    orchestration: Dict[str, Any]
    usage_ledger: Optional[Dict[str, Any]] = None
    session_title: Optional[str] = None
    session_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def project_turn(
    result: Dict[str, Any],
    *,
    context: Optional[EgressContext] = None,
) -> PublicTurn:
    ctx = context or EgressContext(
        engine=str(result.get("engine") or "supervisor"),
        interaction_mode=str(result.get("interaction_mode") or "ask"),
    )
    orch = result.get("orchestration") if isinstance(result.get("orchestration"), dict) else {}
    strategy = str(orch.get("strategy") or result.get("strategy") or "ask")
    reply_cap = 48000 if strategy == "document_content_dump" else 8000
    active = str(result.get("active_subagent") or orch.get("active_subagent") or "ask")
    upgrade = result.get("upgrade_hint") or orch.get("upgrade_hint")
    fork_jobs = result.get("fork_jobs_enqueued") or orch.get("fork_jobs_enqueued") or []
    if not isinstance(fork_jobs, list):
        fork_jobs = []
    denials = _public_permission_denials(
        result.get("permission_denials") or orch.get("permission_denials") or []
    )
    public_orch = _public_orchestration(
        orch,
        strategy=strategy,
        active_subagent=active,
        interaction_mode=str(result.get("interaction_mode") or ctx.interaction_mode),
        upgrade_hint=upgrade if isinstance(upgrade, dict) else None,
        fork_jobs=fork_jobs,
        denials=denials,
    )
    reply = sanitize_user_reply(str(result.get("reply") or ""), max_len=reply_cap)
    tool_calls = sanitize_tool_calls(result.get("tool_calls"))
    thinking_steps = sanitize_thinking_steps(
        result.get("thinking_steps") or orch.get("thinking_steps") or []
    )
    artifacts = extract_artifact_refs(result.get("tool_calls"))
    seen_artifact_ids = {ref["artifact_id"] for ref in artifacts}
    for raw in result.get("artifacts") or []:
        ref = normalize_artifact_ref(raw)
        if ref and ref["artifact_id"] not in seen_artifact_ids:
            artifacts.append(ref)
            seen_artifact_ids.add(ref["artifact_id"])
    explicit_parts = result.get("parts")
    parts = normalize_message_parts(explicit_parts, fallback_text="") or build_message_parts(
        reply=reply,
        tool_calls=tool_calls,
        orchestration=public_orch,
        upgrade_hint=upgrade if isinstance(upgrade, dict) else None,
        artifacts=artifacts,
        permission_denials=denials,
    )
    turn_id = str(result.get("turn_id") or new_turn_id())
    outcome = derive_turn_outcome(result)
    events = build_turn_events(
        turn_id=turn_id,
        session_id=result.get("session_id"),
        parts=parts,
        reply=reply,
        orchestration=public_orch,
        artifacts=artifacts,
        outcome=outcome,
    )
    return PublicTurn(
        turn_id=turn_id,
        outcome=outcome,
        reply=reply,
        tool_calls=tool_calls,
        thinking_steps=thinking_steps,
        parts=parts,
        artifacts=artifacts,
        events=events,
        session_id=result.get("session_id"),
        elapsed_seconds=float(result.get("elapsed_seconds") or 0),
        active_subagent=active,
        engine=str(result.get("engine") or ctx.engine),
        interaction_mode=str(result.get("interaction_mode") or ctx.interaction_mode),
        upgrade_hint=upgrade if isinstance(upgrade, dict) else None,
        fork_jobs_enqueued=[str(x) for x in fork_jobs],
        permission_denials=denials,
        orchestration=public_orch,
        usage_ledger=_public_usage_ledger(result.get("usage_ledger")),
        session_title=_sanitize_title(result.get("session_title")),
        session_summary=_sanitize_summary(result.get("session_summary")),
    )


def _public_usage_ledger(value: Any) -> Optional[Dict[str, Any]]:
    """Expose accounting only; never copy arbitrary provider metadata or prompts."""
    if not isinstance(value, dict):
        return None
    calls = []
    for raw in value.get("calls") or []:
        if not isinstance(raw, dict):
            continue
        usage = raw.get("usage")
        clean_usage = {
            key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage, dict) and type(usage.get(key)) is int and usage[key] >= 0
        } if isinstance(usage, dict) else None
        calls.append({
            "call_id": str(raw.get("call_id") or "")[:80],
            "stage": str(raw.get("stage") or "")[:80],
            "provider": str(raw.get("provider") or "")[:80],
            "model": str(raw.get("model") or "")[:120],
            "usage": clean_usage if clean_usage else None,
            "finish_reason": str(raw.get("finish_reason") or "")[:80],
        })
    cap = value.get("turn_token_cap")
    return {
        "mode": str(value.get("mode") or "")[:40],
        "usage_turn_id": str(value.get("usage_turn_id") or "")[:64],
        "background_jobs": [
            {key: str(job.get(key) or "")[:80]
             for key in ("job_id", "kind", "status", "resume_state")}
            for job in value.get("background_jobs", []) if isinstance(job, dict)
        ],
        "turn_token_cap": cap if type(cap) is int and cap > 0 else None,
        "verified": bool(calls) and all(
            c["usage"] and all(k in c["usage"] for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
            and c["usage"]["total_tokens"] >= c["usage"]["prompt_tokens"] + c["usage"]["completion_tokens"]
            for c in calls
        ),
        "calls": calls,
    }


def project_session_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """Public context view: no raw blackboard or internal orchestration."""
    ctx = context if isinstance(context, dict) else {}
    imode = str(ctx.get("interaction_mode") or "ask")
    orch = ctx.get("orchestration") if isinstance(ctx.get("orchestration"), dict) else {}
    return {
        "interaction_mode": imode,
        "orchestration": _public_orchestration(
            orch,
            strategy=str(orch.get("strategy") or ""),
            active_subagent=str(orch.get("active_subagent") or ""),
            interaction_mode=imode,
            upgrade_hint=orch.get("upgrade_hint") if isinstance(orch.get("upgrade_hint"), dict) else None,
            fork_jobs=orch.get("fork_jobs_enqueued") if isinstance(orch.get("fork_jobs_enqueued"), list) else [],
            denials=_public_permission_denials(orch.get("permission_denials") or []),
        ),
    }


def project_session_messages(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    public: List[Dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        out = {
            "role": role,
            "content": sanitize_user_reply(content, max_len=8000) if role == "assistant" else content,
        }
        for key in ("created_at", "timestamp"):
            if msg.get(key):
                out[key] = msg[key]
        meta = _public_message_metadata(msg.get("metadata") or {})
        if msg.get("message_id"):
            out["id"] = str(msg["message_id"])
        if meta.get("turn_id"):
            out["turn_id"] = meta["turn_id"]
        if meta.get("interaction_mode"):
            out["interaction_mode"] = meta["interaction_mode"]
        if meta.get("outcome"):
            out["outcome"] = meta["outcome"]
        parts = normalize_message_parts(meta.get("parts_v1"), fallback_text=content)
        if parts:
            out["parts"] = parts
        if meta:
            out["metadata"] = meta
        public.append(out)
    return public


def _public_message_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    out: Dict[str, Any] = {}
    if meta.get("engine"):
        out["engine"] = str(meta["engine"])
    if meta.get("turn_id"):
        out["turn_id"] = str(meta["turn_id"])
    if meta.get("interaction_mode"):
        out["interaction_mode"] = str(meta["interaction_mode"])
    outcome = normalize_turn_outcome(meta.get("outcome"))
    if outcome:
        out["outcome"] = outcome
    if isinstance(meta.get("parts_v1"), list):
        out["parts_v1"] = normalize_message_parts(meta.get("parts_v1"))
    if isinstance(meta.get("artifacts"), list):
        out["artifacts"] = _drop_forbidden_keys(meta["artifacts"])
    if isinstance(meta.get("upgrade_hint"), dict):
        out["upgrade_hint"] = {
            "to": str(meta["upgrade_hint"].get("to") or ""),
            "reason": sanitize_user_reply(str(meta["upgrade_hint"].get("reason") or ""), max_len=160),
        }
    if isinstance(meta.get("tool_calls"), list):
        out["tool_calls"] = sanitize_tool_calls(meta.get("tool_calls"))
    if isinstance(meta.get("orchestration"), dict):
        out["orchestration"] = _public_orchestration(
            meta["orchestration"],
            strategy=str(meta["orchestration"].get("strategy") or ""),
            active_subagent=str(meta["orchestration"].get("active_subagent") or ""),
            interaction_mode=str(meta["orchestration"].get("interaction_mode") or "ask"),
            upgrade_hint=meta["orchestration"].get("upgrade_hint")
            if isinstance(meta["orchestration"].get("upgrade_hint"), dict)
            else None,
            fork_jobs=meta["orchestration"].get("fork_jobs_enqueued")
            if isinstance(meta["orchestration"].get("fork_jobs_enqueued"), list)
            else [],
            denials=_public_permission_denials(meta["orchestration"].get("permission_denials") or []),
        )
    return out


def _public_orchestration(
    orch: Dict[str, Any],
    *,
    strategy: str,
    active_subagent: str,
    interaction_mode: str,
    upgrade_hint: Optional[Dict[str, Any]],
    fork_jobs: List[Any],
    denials: List[Dict[str, str]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "strategy": strategy,
        "active_subagent": active_subagent,
        "interaction_mode": interaction_mode,
    }
    if upgrade_hint:
        out["upgrade_hint"] = {
            "to": str(upgrade_hint.get("to") or ""),
            "reason": sanitize_user_reply(str(upgrade_hint.get("reason") or ""), max_len=160),
        }
    if fork_jobs:
        out["fork_jobs_enqueued"] = [str(x) for x in fork_jobs]
    if denials:
        out["permission_denials"] = denials
    progress = _public_progress(orch.get("supervisor_view") if isinstance(orch, dict) else None)
    # Assistant messages persist the already-public orchestration projection,
    # not the private ``supervisor_view``. Re-projecting that metadata through
    # history must retain its safe evidence/source coverage instead of
    # silently dropping it. The persisted shape is still re-whitelisted here;
    # it is not trusted as arbitrary output.
    if not progress and isinstance(orch, dict):
        progress = _public_persisted_progress(orch.get("progress"))
    if progress:
        out["progress"] = progress
    mount_errors = orch.get("mount_errors") if isinstance(orch, dict) else None
    if isinstance(mount_errors, list) and mount_errors:
        out["mount_errors"] = [sanitize_user_reply(str(x), max_len=160) for x in mount_errors[:3]]
    return _drop_forbidden_keys(out)


def _public_persisted_progress(raw: Any) -> Dict[str, Any]:
    """Re-validate an already-public progress projection stored in history."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    if isinstance(raw.get("path"), str):
        out["path"] = sanitize_user_reply(raw["path"], max_len=80)
    grounding = raw.get("grounding")
    if isinstance(grounding, dict) and grounding.get("status") in {
        "model_checked", "blocked", "not_applicable", "not_checked", "source_count",
    }:
        out["grounding"] = {
            key: grounding[key]
            for key in ("status", "checks", "revised", "reason")
            if key in grounding
        }
    if "document_complete" in raw:
        out["document_complete"] = bool(raw.get("document_complete"))
    role_labels = {
        "profile": "职业画像",
        "match": "岗位匹配",
        "report": "职业报告",
        "planner": "行动规划",
        "qa": "资料问答",
    }
    if isinstance(raw.get("executed_steps"), list):
        executed = []
        for row in raw["executed_steps"][:6]:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "")
            status = str(row.get("status") or "")
            if role in role_labels and status in {"completed", "not_completed"}:
                executed.append({"label": role_labels[role], "status": status})
        if executed:
            out["executed_steps"] = executed
    if isinstance(raw.get("evidence"), dict):
        evidence = raw["evidence"]
        coverage = evidence.get("coverage") if isinstance(evidence.get("coverage"), dict) else {}
        safe_coverage: Dict[str, Any] = {}
        if type(coverage.get("complete")) is bool:
            safe_coverage["complete"] = coverage["complete"]
        for key in ("returned", "total"):
            value = coverage.get(key)
            if type(value) is int and value >= 0:
                safe_coverage[key] = value
        reason = coverage.get("reason")
        if isinstance(reason, str) and reason in _PUBLIC_EVIDENCE_COVERAGE_REASONS:
            safe_coverage["reason"] = reason
        safe_evidence: Dict[str, Any] = {
            "status": _public_evidence_status(evidence.get("status")),
            "coverage": safe_coverage,
            "has_more": bool(evidence.get("has_more")),
            "next_offset": evidence.get("next_offset")
            if type(evidence.get("next_offset")) is int and evidence["next_offset"] >= 0 else None,
            "truncated": bool(evidence.get("truncated")),
        }
        next_action = evidence.get("next_action")
        if next_action in {"continue_evidence", "retry_evidence"}:
            safe_evidence["next_action"] = next_action
        sources = []
        for source in evidence.get("sources") or []:
            if not isinstance(source, dict):
                continue
            sources.append({
                "uri": sanitize_user_reply(str(source.get("uri") or ""), max_len=160),
                "section": sanitize_user_reply(str(source.get("section") or ""), max_len=80),
                "version": sanitize_user_reply(str(source.get("version") or ""), max_len=160),
                "status": sanitize_user_reply(str(source.get("status") or ""), max_len=80),
            })
        if sources:
            safe_evidence["sources"] = sources[:4]
        out["evidence"] = safe_evidence
    return out


def _public_progress(view: Any) -> Dict[str, Any]:
    if not isinstance(view, dict):
        return {}
    out: Dict[str, Any] = {}
    if view.get("path"):
        out["path"] = str(view["path"])
    grounding = view.get("grounding")
    if isinstance(grounding, dict) and grounding.get("status") in {"model_checked", "blocked", "not_applicable", "not_checked", "source_count"}:
        out["grounding"] = {key: grounding[key] for key in ("status", "checks", "revised", "reason") if key in grounding}
    if isinstance(view.get("filenames"), list):
        out["filenames"] = [sanitize_user_reply(str(x), max_len=120) for x in view["filenames"][:4]]
    doc_pack = view.get("doc_pack") if isinstance(view.get("doc_pack"), dict) else {}
    if "all_complete" in doc_pack:
        out["document_complete"] = bool(doc_pack.get("all_complete"))
    if "allow_tools" in view:
        out["allow_tools"] = bool(view.get("allow_tools"))
    if "lead_action" in view:
        out["lead_action"] = str(view.get("lead_action") or "")
    if isinstance(view.get("missing_slots"), list):
        out["missing_slots"] = [str(x) for x in view["missing_slots"][:4]]
    if isinstance(view.get("plan_sketch"), list):
        out["plan_sketch"] = [sanitize_user_reply(str(x), max_len=120) for x in view["plan_sketch"][:4]]
    role_labels = {
        "profile": "职业画像",
        "match": "岗位匹配",
        "report": "职业报告",
        "planner": "行动规划",
        "qa": "资料问答",
    }
    if isinstance(view.get("executed_steps"), list):
        executed = []
        for row in view["executed_steps"][:6]:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "")
            status = str(row.get("status") or "")
            if role not in role_labels or status not in {"completed", "not_completed"}:
                continue
            executed.append({"label": role_labels[role], "status": status})
        if executed:
            out["executed_steps"] = executed
    evidence = view.get("evidence_pack") if isinstance(view.get("evidence_pack"), dict) else {}
    if evidence:
        coverage = evidence.get("coverage") if isinstance(evidence.get("coverage"), dict) else {}
        public_coverage: Dict[str, Any] = {}
        if type(coverage.get("complete")) is bool:
            public_coverage["complete"] = coverage["complete"]
        for key in ("returned", "total"):
            value = coverage.get(key)
            if type(value) is int and value >= 0:
                public_coverage[key] = value
            elif key == "total" and value is None and key in coverage:
                public_coverage[key] = None
        reason = coverage.get("reason")
        if isinstance(reason, str) and reason in _PUBLIC_EVIDENCE_COVERAGE_REASONS:
            public_coverage["reason"] = reason
        has_more = bool(evidence.get("has_more"))
        unavailable = bool(evidence.get("unavailable")) or evidence.get("status") == "unavailable"
        if has_more:
            next_action = "continue_evidence"
        elif unavailable or evidence.get("budget_exceeded"):
            next_action = "retry_evidence"
        else:
            next_action = None
        public_evidence: Dict[str, Any] = {
            "status": _public_evidence_status(evidence.get("status")),
            "coverage": public_coverage,
            "has_more": has_more,
            "next_offset": evidence.get("next_offset")
            if type(evidence.get("next_offset")) is int else None,
            "truncated": bool(evidence.get("truncated")),
        }
        if next_action:
            public_evidence["next_action"] = next_action
        sources = []
        for source in evidence.get("sources") or []:
            if not isinstance(source, dict):
                continue
            sources.append({
                "uri": sanitize_user_reply(str(source.get("uri") or ""), max_len=160),
                "section": sanitize_user_reply(str(source.get("section") or ""), max_len=80),
                "version": sanitize_user_reply(str(source.get("version") or ""), max_len=160),
                "status": sanitize_user_reply(str(source.get("status") or ""), max_len=80),
            })
        if sources:
            public_evidence["sources"] = sources[:4]
        out["evidence"] = public_evidence
    return out


def _public_evidence_status(raw: Any) -> str:
    """Expose only stable coverage states, never arbitrary evidence strings."""
    return raw if isinstance(raw, str) and raw in _PUBLIC_EVIDENCE_STATUSES else "unavailable"


def _public_permission_denials(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("reason_code") or "permission_denied")
        message = (
            item.get("public_message")
            or item.get("message")
            or "当前模式未允许这项操作。"
        )
        out.append({
            "code": sanitize_user_reply(code, max_len=80),
            "message": sanitize_user_reply(str(message), max_len=160),
        })
    return out


def _drop_forbidden_keys(data: Any) -> Any:
    if isinstance(data, dict):
        cleaned: Dict[str, Any] = {}
        for key, value in data.items():
            lk = str(key).lower()
            if lk in NEVER_KEYS or lk.endswith("_json") or lk.endswith("_token"):
                continue
            if lk in {
                "supervisor_view", "blackboard_refs", "blackboard",
                "plan_results", "file_ids", "raw_args", "args",
            }:
                continue
            cleaned[str(key)] = _drop_forbidden_keys(value)
        return cleaned
    if isinstance(data, list):
        return [_drop_forbidden_keys(v) for v in data]
    return data


def _sanitize_title(raw: Any) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    cleaned = sanitize_user_reply(raw.strip(), max_len=60)
    return cleaned or None


def _sanitize_summary(raw: Any) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    cleaned = sanitize_user_reply(raw.strip(), max_len=200)
    return cleaned or None
