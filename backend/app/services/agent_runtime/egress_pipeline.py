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
    extract_artifact_refs,
    new_turn_id,
    normalize_message_parts,
)


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
    events = build_turn_events(
        turn_id=turn_id,
        session_id=result.get("session_id"),
        parts=parts,
        reply=reply,
        orchestration=public_orch,
        artifacts=artifacts,
    )
    return PublicTurn(
        turn_id=turn_id,
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
    )


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
    if progress:
        out["progress"] = progress
    mount_errors = orch.get("mount_errors") if isinstance(orch, dict) else None
    if isinstance(mount_errors, list) and mount_errors:
        out["mount_errors"] = [sanitize_user_reply(str(x), max_len=160) for x in mount_errors[:3]]
    return _drop_forbidden_keys(out)


def _public_progress(view: Any) -> Dict[str, Any]:
    if not isinstance(view, dict):
        return {}
    out: Dict[str, Any] = {}
    if view.get("path"):
        out["path"] = str(view["path"])
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
    return out


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
