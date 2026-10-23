"""MySQL-authoritative persistence for owner-bound Agent runs.

Runs, plans, steps, events, and the initial dispatch outbox record are committed
to MySQL. Celery remains transport only; an uncertain side effect is recorded
as ``needs_reconciliation`` and is never made runnable by this repository.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from dataclasses import replace
from typing import Any, Dict, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .plan import validate_plan_steps
from .agent_task_control import (
    ContractPatch,
    ControlIntent,
    ReadGrant,
    StaleTaskRevision,
    StaleTaskOutput,
    TaskBudget,
    TaskContract,
    TaskControlError,
    WriteGrant,
    decide_control,
    initial_task_contract,
    parse_control_message,
    revise_contract,
    task_contract_from_record,
    task_contract_to_record,
    write_is_authorized,
)


_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_RUN_ID = re.compile(r"^run_[a-f0-9]{32}$")
_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_RESULT_REFERENCE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,256}$")
_INPUT_SNAPSHOT_VERSION = 1
_INPUT_SNAPSHOT_REDACTION_VERSION = 1
_MAX_INPUT_MESSAGE_BYTES = 48 * 1024
_MAX_INPUT_SNAPSHOT_BYTES = 64 * 1024
_MAX_RESULT_BYTES = 32 * 1024
_MAX_FINAL_REPLY_BYTES = 24 * 1024
_MAX_TASK_CONTRACT_BYTES = 32 * 1024

# Server-owned defaults. Client messages cannot increase any of these limits.
_DEFAULT_TASK_BUDGET = TaskBudget(
    max_model_calls=24,
    max_tool_calls=32,
    max_input_tokens=24_000,
    max_output_tokens=8_000,
    max_call_input_tokens=12_000,
    max_call_output_tokens=4_000,
    max_retries=0,
)

RUN_STATUSES = frozenset({
    "queued", "planning", "planned", "running", "steps_completed",
    "finalizing", "needs_reconciliation", "completed", "failed", "cancelled",
    "waiting_input", "waiting_authorization", "pause_requested", "paused", "partial",
})
STEP_STATUSES = frozenset({
    "pending", "running", "completed", "failed", "needs_reconciliation",
    "cancelled", "superseded",
})
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "partial"})
EVENT_TYPES = frozenset({
    "run.submitted", "run.planning", "run.plan_created", "run.plan_revised",
    "run.step_started", "run.step_completed", "run.step_failed",
    "run.step_needs_reconciliation", "run.cancel_requested", "run.cancelled",
    "run.steps_completed", "run.finalizing", "run.completed",
    "run.dispatch_published", "run.dispatch_claimed", "run.dispatch_requeued",
    "run.dispatch_retry_scheduled", "run.dispatch_needs_reconciliation",
    "run.executor_unavailable", "run.failed",
    "run.resource_read_started", "run.resource_read_completed",
    "run.business_delegated",
    "run.contract_created", "run.contract_revised", "run.step_superseded",
    "run.pause_requested", "run.paused", "run.resumed",
    "run.waiting_input", "run.waiting_authorization", "run.partial",
    "run.artifact_save_confirmed", "run.artifact_saved", "run.finalization_superseded",
    "run.followup_classification_waiting", "run.followup_received",
    "run.followup_classification_started", "run.followup_classified",
    "run.followup_needs_reconciliation", "run.followup_budget_blocked",
    "run.followup_applied",
})


class AgentRunStoreError(RuntimeError):
    """Base error raised by the durable Agent run store."""


class AgentRunNotFound(AgentRunStoreError):
    """Run does not exist for the supplied owner."""


class AgentRunConflict(AgentRunStoreError):
    """A stale or unsafe run transition was rejected."""


class AgentRunIdempotencyConflict(AgentRunConflict):
    """An idempotency key was already used for a different semantic request."""


def _utcnow() -> datetime:
    # MySQL DATETIME(6) is stored in UTC by this repository; the value is naive
    # only because MySQL DATETIME has no timezone type.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _canonical_json(value: Any, *, max_bytes: int) -> tuple[str, str]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Agent run data must be JSON serializable") from exc
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("Agent run data exceeds its storage bound")
    return encoded, digest


def _decode_json(value: Any, *, expected_type: type, field: str) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AgentRunStoreError(f"Persisted {field} JSON is invalid") from exc
    if not isinstance(value, expected_type):
        raise AgentRunStoreError(f"Persisted {field} has an invalid shape")
    return value


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _owner_value(value: Any, *, field: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 64:
        raise ValueError(f"{field} must contain 1..64 characters")
    return result


def _run_id_value(value: Any) -> str:
    result = str(value or "")
    if not _RUN_ID.fullmatch(result):
        raise ValueError("run_id is invalid")
    return result


def _safe_event_reason(value: Any) -> str:
    reason = str(value or "").strip()
    if not reason or len(reason) > 64 or not _ERROR_CODE.fullmatch(reason):
        raise ValueError("event reason must be a bounded safe code")
    return reason


def _normalize_input_snapshot(payload: Mapping[str, Any]) -> tuple[Dict[str, Any], str, str]:
    """Build the only durable request envelope this repository accepts.

    This is a field allowlist, not a claim that arbitrary text is secret-free:
    headers, cookies, auth tokens and unknown envelope fields are never copied
    into the run row, while the user-authored message is preserved byte-for-
    byte after UTF-8 encoding so that a future worker can reconstruct intent.
    """
    allowed = {"message", "interaction_mode", "expert_id", "capability_id", "attachment_ids", "task_context_version"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"request_payload contains unsupported fields: {sorted(map(str, unknown))}")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip() or "\x00" in message:
        raise ValueError("request_payload.message must be non-empty text without NUL")
    message_bytes = len(message.encode("utf-8"))
    if message_bytes > _MAX_INPUT_MESSAGE_BYTES:
        raise ValueError("request_payload.message exceeds 48 KiB")

    interaction_mode = payload.get("interaction_mode", "agent")
    if not isinstance(interaction_mode, str) or interaction_mode not in {"agent", "experts"}:
        raise ValueError("interaction_mode must be agent or experts")

    expert_id = payload.get("expert_id")
    capability_id = payload.get("capability_id")
    if (expert_id is None) != (capability_id is None):
        raise ValueError("expert_id and capability_id must be supplied together")
    for value, field in ((expert_id, "expert_id"), (capability_id, "capability_id")):
        if value is not None and (not isinstance(value, str) or not _SAFE_REFERENCE.fullmatch(value)):
            raise ValueError(f"{field} must be 1..128 safe ASCII characters")
    from .expert_capabilities import resolve_explicit_capability

    # This is a persistence trust boundary as well as an HTTP contract. An
    # unknown or mode-incompatible explicit package must never be stored for a
    # later worker to reinterpret as an unselected legacy run.
    resolve_explicit_capability({
        "expert_id": expert_id,
        "capability_id": capability_id,
        "interaction_mode": interaction_mode,
    })

    attachment_ids = payload.get("attachment_ids", [])
    if not isinstance(attachment_ids, list) or len(attachment_ids) > 20:
        raise ValueError("attachment_ids must be a list of at most 20 owner-resolved references")
    normalized_attachment_ids = []
    seen = set()
    for attachment_id in attachment_ids:
        if not isinstance(attachment_id, str) or not _SAFE_REFERENCE.fullmatch(attachment_id):
            raise ValueError("attachment_ids must contain 1..128 safe ASCII references")
        if attachment_id in seen:
            raise ValueError("attachment_ids must not contain duplicates")
        seen.add(attachment_id)
        normalized_attachment_ids.append(attachment_id)

    snapshot = {
        "schema_version": _INPUT_SNAPSHOT_VERSION,
        "redaction_version": _INPUT_SNAPSHOT_REDACTION_VERSION,
        "message": message,
        "interaction_mode": interaction_mode,
        "expert_id": expert_id,
        "capability_id": capability_id,
        "attachment_ids": normalized_attachment_ids,
    }
    if 'task_context_version' in payload:
        version = payload['task_context_version']
        if type(version) is not int or version < 0:
            raise ValueError('task_context_version must be a nonnegative integer')
        snapshot['task_context_version'] = version
    encoded, digest = _canonical_json(snapshot, max_bytes=_MAX_INPUT_SNAPSHOT_BYTES)
    return snapshot, encoded, digest


def _normalize_final_result(value: Mapping[str, Any]) -> Dict[str, Any]:
    allowed = {
        "reply", "result_ref", "format", "candidate_reply_unverified",
        "planner_model_usage", "finalizer_model_usage", "finalizer_error_code",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"final_result contains unsupported fields: {sorted(map(str, unknown))}")
    normalized: Dict[str, Any] = {}
    reply = value.get("reply")
    if reply is not None:
        if not isinstance(reply, str) or not reply.strip() or "\x00" in reply:
            raise ValueError("final_result.reply must be non-empty text without NUL")
        if len(reply.encode("utf-8")) > _MAX_FINAL_REPLY_BYTES:
            raise ValueError("final_result.reply exceeds 24 KiB")
        normalized["reply"] = reply
    result_ref = value.get("result_ref")
    if result_ref is not None:
        if not isinstance(result_ref, str) or not _SAFE_RESULT_REFERENCE.fullmatch(result_ref):
            raise ValueError("final_result.result_ref must be a safe reference")
        normalized["result_ref"] = result_ref
    if "reply" not in normalized and "result_ref" not in normalized:
        raise ValueError("final_result must include reply or result_ref")
    result_format = value.get("format")
    if result_format is not None:
        if not isinstance(result_format, str) or result_format not in {"text", "structured"}:
            raise ValueError("final_result.format must be text or structured")
        normalized["format"] = result_format
    candidate = value.get("candidate_reply_unverified")
    if candidate is not None:
        if (not isinstance(candidate, str) or "\x00" in candidate
                or len(candidate.encode("utf-8")) > 16 * 1024):
            raise ValueError("candidate_reply_unverified must be bounded text without NUL")
        normalized["candidate_reply_unverified"] = candidate
    for field in ("planner_model_usage", "finalizer_model_usage"):
        phase_usage = value.get(field)
        if phase_usage is not None:
            normalized[field] = _normalize_model_usage_projection(phase_usage)
    finalizer_error = value.get("finalizer_error_code")
    if finalizer_error is not None:
        if not isinstance(finalizer_error, str) or not _ERROR_CODE.fullmatch(finalizer_error):
            raise ValueError("finalizer_error_code is invalid")
        normalized["finalizer_error_code"] = finalizer_error
    return normalized


def _normalize_model_usage_projection(value: Any) -> Dict[str, Any]:
    """Validate the executor's bounded provider-usage record before storage."""
    fields = {
        "schema_version", "status", "reason", "turn_token_cap",
        "call_count", "reported_total_tokens", "calls",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("finalizer_model_usage has an invalid shape")
    if value.get("schema_version") != 1 or value.get("status") not in {
        "observed", "unverified",
    }:
        raise ValueError("finalizer_model_usage version or status is invalid")
    reason = value.get("reason")
    if not isinstance(reason, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", reason):
        raise ValueError("finalizer_model_usage reason is invalid")
    cap = value.get("turn_token_cap")
    if cap is not None and (type(cap) is not int or not 1 <= cap <= 2_000_000):
        raise ValueError("finalizer_model_usage token cap is invalid")
    call_count = value.get("call_count")
    if type(call_count) is not int or not 0 <= call_count <= 32:
        raise ValueError("finalizer_model_usage call count is invalid")
    total = value.get("reported_total_tokens")
    if total is not None and (type(total) is not int or not 0 <= total <= 2_000_000):
        raise ValueError("finalizer_model_usage total is invalid")
    calls = value.get("calls")
    if not isinstance(calls, list) or len(calls) != call_count or len(calls) > 32:
        raise ValueError("finalizer_model_usage calls are invalid")
    safe_calls = []
    for index, call in enumerate(calls, 1):
        if not isinstance(call, Mapping) or set(call) != {
            "index", "stage", "provider", "model", "usage", "finish_reason",
            "preflight_reason",
        }:
            raise ValueError("finalizer_model_usage call has an invalid shape")
        if call.get("index") != index:
            raise ValueError("finalizer_model_usage call indices are invalid")
        row = {}
        for field, limit in (
            ("stage", 64), ("provider", 48), ("model", 128),
            ("finish_reason", 32), ("preflight_reason", 64),
        ):
            item = call.get(field)
            if not isinstance(item, str) or len(item) > limit or "\x00" in item:
                raise ValueError(f"finalizer_model_usage {field} is invalid")
            row[field] = item
        usage = call.get("usage")
        if usage is not None:
            if not isinstance(usage, Mapping) or set(usage) != {
                "prompt_tokens", "completion_tokens", "total_tokens",
            } or any(type(usage.get(field)) is not int or usage[field] < 0
                     for field in ("prompt_tokens", "completion_tokens", "total_tokens")):
                raise ValueError("finalizer_model_usage provider tokens are invalid")
            row["usage"] = {field: usage[field] for field in (
                "prompt_tokens", "completion_tokens", "total_tokens",
            )}
        else:
            row["usage"] = None
        safe_calls.append({"index": index, **row})
    return {
        "schema_version": 1, "status": value["status"], "reason": reason,
        "turn_token_cap": cap, "call_count": call_count,
        "reported_total_tokens": total, "calls": safe_calls,
    }


class AgentRunRepository:
    """Transactional run/plan/step/event store; MySQL is the authority.

    The unique request key is stored only as a domain-separated SHA-256. The
    semantic request is stored as a small, versioned, allowlisted snapshot so
    a future worker can reconstruct it after Flask restarts. Transport secrets
    and arbitrary caller fields are rejected rather than persisted. Each
    mutation locks the run row, increments ``last_event_seq``, updates the
    snapshot and inserts its event in the same transaction.
    """

    def __init__(self, mysql=None):
        self._mysql = mysql

    @property
    def mysql(self):
        if self._mysql is None:
            from ..db.mysql_client import get_mysql_client

            self._mysql = get_mysql_client()
        return self._mysql

    @staticmethod
    def _task_contract_schema_available(conn) -> bool:
        from sqlalchemy import inspect

        inspector = inspect(conn)
        columns = {item["name"] for item in inspector.get_columns("agent_run")}
        required = {"current_contract_revision", "pause_requested_at"}
        tables_ready = (
            inspector.has_table("agent_run_task_contract_revision")
            and inspector.has_table("agent_run_task_write_confirmation")
            and inspector.has_table("agent_run_task_followup")
        )
        if required.issubset(columns) and tables_ready:
            return True
        if conn.dialect.name == "sqlite":
            # Older isolated run-store fixtures intentionally exercise only
            # the pre-contract API. Production MySQL fails closed until 0021.
            return False
        raise AgentRunStoreError("Agent task control migration 0021 is not applied")

    def _task_contract_for_row(self, conn, run) -> Optional[TaskContract]:
        if not self._task_contract_schema_available(conn):
            return None
        revision = int(run.get("current_contract_revision") or 0)
        if revision == 0:
            return None
        row = conn.execute(text("""
            SELECT contract_json, contract_sha256
            FROM agent_run_task_contract_revision
            WHERE run_id=:run_id AND student_id=:student_id AND revision=:revision
        """), {
            "run_id": run["run_id"], "student_id": run["student_id"],
            "revision": revision,
        }).mappings().first()
        if row is None:
            raise AgentRunStoreError("Current Agent task contract revision is missing")
        record = _decode_json(row["contract_json"], expected_type=dict, field="task contract")
        encoded, digest = _canonical_json(record, max_bytes=_MAX_TASK_CONTRACT_BYTES)
        if digest != row["contract_sha256"]:
            raise AgentRunStoreError("Persisted Agent task contract hash does not match")
        try:
            contract = task_contract_from_record(record)
        except TaskControlError as exc:
            raise AgentRunStoreError("Persisted Agent task contract is invalid") from exc
        if contract.task_id != run["run_id"] or contract.revision != revision:
            raise AgentRunStoreError("Persisted Agent task contract binding does not match")
        return contract

    @staticmethod
    def _insert_task_contract_revision(
        conn, run, contract: TaskContract, *, mutation_kind: str,
        request_sha256: str, idempotency_key_sha256: Optional[str] = None,
    ) -> None:
        record = task_contract_to_record(contract)
        encoded, digest = _canonical_json(record, max_bytes=_MAX_TASK_CONTRACT_BYTES)
        conn.execute(text("""
            INSERT INTO agent_run_task_contract_revision
            (run_id, student_id, revision, contract_json, contract_sha256,
             idempotency_key_sha256, request_sha256, mutation_kind, created_at)
            VALUES (:run_id, :student_id, :revision, :contract_json, :contract_sha256,
                    :idempotency_key_sha256, :request_sha256, :mutation_kind, :created_at)
        """), {
            "run_id": contract.task_id, "student_id": run["student_id"],
            "revision": contract.revision, "contract_json": encoded,
            "contract_sha256": digest,
            "idempotency_key_sha256": idempotency_key_sha256,
            "request_sha256": request_sha256, "mutation_kind": mutation_kind,
            "created_at": _utcnow(),
        })

    def get_task_contract_for_owner(self, run_id: str, *, student_id: str) -> Optional[TaskContract]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._read_connection() as conn:
            with conn.begin():
                run = self._select_run(conn, run_id=run_id, student_id=student_id)
                if run is None:
                    raise AgentRunNotFound("Agent run not found")
                return self._task_contract_for_row(conn, run)

    def _saved_artifacts_for_row(self, conn, run) -> list[Dict[str, Any]]:
        if not self._task_contract_schema_available(conn):
            return []
        rows = conn.execute(text("""
            SELECT contract_revision, plan_version, plan_sha256, confirmation_id,
                   artifact_id, artifact_saved_at
            FROM agent_run_task_write_confirmation
            WHERE run_id=:run_id AND student_id=:student_id AND artifact_saved_at IS NOT NULL
            ORDER BY plan_version
        """), {"run_id": run["run_id"], "student_id": run["student_id"]}).mappings().all()
        return [{
            "contract_revision": int(row["contract_revision"]),
            "plan_version": int(row["plan_version"]),
            "plan_sha256": str(row["plan_sha256"]),
            "confirmation_id": str(row["confirmation_id"]),
            "artifact_id": str(row["artifact_id"]),
            "saved_at": _iso(row["artifact_saved_at"]),
        } for row in rows]

    def ensure_initial_task_contract(
        self, run_id: str, *, student_id: str, claim_token: Optional[str] = None,
    ) -> Optional[TaskContract]:
        """Backfill a legacy run once, under its owner row lock and worker claim."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if not self._task_contract_schema_available(conn):
                return None
            current = self._task_contract_for_row(conn, run)
            if current is not None:
                return current
            snapshot = self._verified_input_snapshot(run)
            contract = initial_task_contract(run_id, snapshot["message"], _DEFAULT_TASK_BUDGET)
            self._insert_task_contract_revision(
                conn, run, contract, mutation_kind="initial",
                request_sha256=run["request_sha256"],
            )
            self._append_event(
                conn, run, event_type="run.contract_created",
                new_current_contract_revision=contract.revision,
                payload={"contract_revision": contract.revision,
                         "contract_sha256": contract.sha256},
            )
            return contract

    def revise_task_contract(
        self,
        run_id: str,
        *,
        student_id: str,
        control: Mapping[str, Any],
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """CAS-apply one user-authored change and fence any stale read phase."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key must be 8..128 safe ASCII characters")
        if not isinstance(control, Mapping):
            raise ValueError("control must be an object")
        request_json, request_sha256 = _canonical_json(dict(control), max_bytes=8192)
        key_sha256 = hashlib.sha256(
            ("cpa-agent-task-control\0v1\0" + run_id + "\0" + student_id + "\0" + idempotency_key)
            .encode("utf-8")
        ).hexdigest()

        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            if not self._task_contract_schema_available(conn):
                raise AgentRunStoreError("Agent task control migration 0021 is not applied")
            previous = conn.execute(text("""
                SELECT revision, request_sha256, contract_json, contract_sha256
                FROM agent_run_task_contract_revision
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            if previous:
                if previous["request_sha256"] != request_sha256:
                    raise AgentRunIdempotencyConflict(
                        "Idempotency-Key already identifies a different task control request"
                    )
                record = _decode_json(previous["contract_json"], expected_type=dict,
                                      field="task contract")
                _, digest = _canonical_json(record, max_bytes=_MAX_TASK_CONTRACT_BYTES)
                if digest != previous["contract_sha256"]:
                    raise AgentRunStoreError("Persisted Agent task contract hash does not match")
                return {"contract": task_contract_to_record(task_contract_from_record(record)),
                        "created": False, "run": self._run_view(run)}

            current = self._task_contract_for_row(conn, run)
            if current is None:
                raise AgentRunConflict("task contract has not been initialized")
            message = parse_control_message(control, current=current)
            if message.intent not in {ControlIntent.SUPPLEMENT, ControlIntent.REVISE_GOAL}:
                raise ValueError("only supplement and revise_goal create a contract revision")
            if run["status"] in TERMINAL_RUN_STATUSES | {"needs_reconciliation"}:
                raise AgentRunConflict("terminal or uncertain task cannot be revised")
            if run.get("cancel_requested_at") is not None:
                raise AgentRunConflict("cancel is pending; task cannot be revised")

            version = int(run["current_plan_version"] or 0)
            active = None
            if version:
                active = conn.execute(text("""
                    SELECT step_index, role, status FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version
                      AND status IN ('running','needs_reconciliation')
                    ORDER BY step_index LIMIT 1
                """), {"run_id": run_id, "version": version}).mappings().first()
            active_status = str(active["status"]) if active else None
            if active and (active_status != "running" or active["role"] != "qa"):
                raise AgentRunConflict(
                    "active or uncertain write step must be reconciled before task revision"
                )
            revision = revise_contract(
                current, message, current_plan_version=version or None,
                task_status=str(run["status"]), active_step_status=active_status,
            )
            if not revision.changed:
                return {"contract": task_contract_to_record(current), "created": False,
                        "run": self._run_view(run)}

            self._insert_task_contract_revision(
                conn, run, revision.contract, mutation_kind=message.intent.value,
                request_sha256=request_sha256, idempotency_key_sha256=key_sha256,
            )
            now = _utcnow()
            if version:
                conn.execute(text("""
                    UPDATE agent_run_step SET status='superseded', finished_at=:now
                    WHERE run_id=:run_id AND plan_version=:version AND status='pending'
                """), {"now": now, "run_id": run_id, "version": version})

            active_read = active_status == "running"
            next_status = "pause_requested" if active_read else "queued"
            self._append_event(
                conn, run, event_type="run.contract_revised", new_status=next_status,
                new_current_contract_revision=revision.contract.revision,
                new_pause_requested_at=now if active_read else None,
                payload={
                    "contract_revision": revision.contract.revision,
                    "contract_sha256": revision.contract.sha256,
                    "supersedes_plan_version": revision.supersedes_plan_version,
                    "invalidates_prior_result": revision.invalidates_prior_result,
                    "idempotency_key_sha256": key_sha256,
                },
            )
            if not active_read:
                dispatch = self._locked_dispatch_row(conn, run_id)
                if not dispatch.get("claim_token"):
                    conn.execute(text("""
                        UPDATE agent_run_dispatch SET dispatch_state='pending',
                            publish_token=NULL, publish_lease_until=NULL,
                            next_publish_at=:now, updated_at=:now
                        WHERE run_id=:run_id
                    """), {"run_id": run_id, "now": now})

        current_run = self.get_for_owner(run_id, student_id=student_id)
        return {"contract": task_contract_to_record(revision.contract),
                "created": True, "run": current_run}

    @staticmethod
    def _followup_key_hash(run_id: str, student_id: str, idempotency_key: str) -> str:
        return hashlib.sha256(
            ("cpa-agent-task-followup\0v1\0" + run_id + "\0" + student_id
             + "\0" + idempotency_key).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _followup_operation_view(row) -> Dict[str, Any]:
        return {
            "status": str(row["operation_status"]),
            "contract_revision": int(row["contract_revision"]),
            "request": _decode_json(row["request_json"], expected_type=dict,
                                     field="task follow-up request"),
            "classification": _decode_json(row["classification_json"], expected_type=dict,
                                            field="task follow-up classification")
            if row["classification_json"] is not None else None,
            "model_usage": _decode_json(row["model_usage_json"], expected_type=dict,
                                         field="task follow-up model usage")
            if row["model_usage_json"] is not None else None,
            "result": _decode_json(row["result_json"], expected_type=dict,
                                    field="task follow-up result")
            if row["result_json"] is not None else None,
            "model_required": bool(row["model_required"]),
            "reserved_model_calls": int(row["reserved_model_calls"] or 0),
            "auto_paused": bool(row["auto_paused"]),
        }

    def begin_task_followup_operation(
        self,
        run_id: str,
        *,
        student_id: str,
        expected_revision: int,
        message: str,
        idempotency_key: str,
        model_required: bool = True,
        classification: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist one owner/run/revision-bound follow-up before model or control work."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError("follow-up message must contain 1..4000 characters")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key must be 8..128 safe ASCII characters")
        if type(model_required) is not bool:
            raise ValueError("model_required must be a boolean")
        if not model_required and not isinstance(classification, Mapping):
            raise ValueError("a deterministic control requires its validated classification")
        request_record = {
            "task_id": run_id,
            "expected_revision": expected_revision,
            "message": message.strip(),
        }
        request_json, request_sha256 = _canonical_json(request_record, max_bytes=8192)
        key_sha256 = self._followup_key_hash(run_id, student_id, idempotency_key)
        classification_json = None
        if classification is not None:
            classification_json, _ = _canonical_json(dict(classification), max_bytes=8192)

        auto_paused = False
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            if not self._task_contract_schema_available(conn):
                raise AgentRunStoreError("Agent follow-up migration 0021 is not applied")
            existing = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise AgentRunIdempotencyConflict(
                        "Idempotency-Key already identifies different follow-up text or revision"
                    )
                return {
                    "created": False,
                    "operation": self._followup_operation_view(existing),
                    "run": self._run_view(run),
                }

            contract = self._task_contract_for_row(conn, run)
            if contract is None:
                raise AgentRunConflict("task contract has not been initialized")
            if contract.revision != expected_revision:
                raise StaleTaskRevision("follow-up is based on a stale contract revision")
            if run["status"] == "needs_reconciliation":
                raise AgentRunConflict("uncertain task must be reconciled before follow-up")

            version = int(run["current_plan_version"] or 0)
            active_step = None
            if version:
                active_step = conn.execute(text("""
                    SELECT status FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version
                      AND status IN ('running','needs_reconciliation')
                    ORDER BY step_index LIMIT 1
                """), {"run_id": run_id, "version": version}).mappings().first()
            if active_step and active_step["status"] == "needs_reconciliation":
                raise AgentRunConflict("uncertain step must be reconciled before follow-up")

            operation_status = "classified" if not model_required else "waiting_safe_point"
            run_status = str(run["status"])
            active_phase = (
                run_status in {"planning", "finalizing", "pause_requested"}
                or bool(active_step and active_step["status"] == "running")
            )
            if model_required and active_phase:
                if run_status != "pause_requested":
                    now = _utcnow()
                    self._append_event(
                        conn, run, event_type="run.pause_requested",
                        new_status="pause_requested", new_pause_requested_at=now,
                        payload={"safe_point_pending": True,
                                 "reason": "task_followup_classification"},
                    )
                    auto_paused = True
            elif model_required and run_status not in {
                "paused", "waiting_input", "waiting_authorization",
                "completed", "failed", "cancelled", "partial",
            }:
                # A stable run is fenced while its follow-up is classified.
                # The worker claim is released only when no phase/step is active.
                now = _utcnow()
                self._append_event(
                    conn, run, event_type="run.paused",
                    new_status="paused", new_pause_requested_at=None,
                    payload={"safe_point": True,
                             "reason": "task_followup_classification"},
                )
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='paused',
                        publish_token=NULL, publish_lease_until=NULL,
                        claim_token=NULL, lease_until=NULL, updated_at=:now
                    WHERE run_id=:run_id
                """), {"run_id": run_id, "now": now})
                auto_paused = True

            now = _utcnow()
            try:
                conn.execute(text("""
                    INSERT INTO agent_run_task_followup
                    (run_id, student_id, idempotency_key_sha256, request_sha256,
                     contract_revision, request_json, operation_status, model_required,
                     reserved_model_calls, auto_paused, active_slot, classification_json,
                     model_usage_json, result_json, created_at, updated_at)
                    VALUES (:run_id, :student_id, :key_sha256, :request_sha256,
                            :revision, :request_json, :status, :model_required,
                            0, :auto_paused, 1, :classification_json, NULL, NULL,
                            :created_at, :updated_at)
                """), {
                    "run_id": run_id, "student_id": student_id,
                    "key_sha256": key_sha256, "request_sha256": request_sha256,
                    "revision": expected_revision, "request_json": request_json,
                    "status": operation_status, "model_required": int(model_required),
                    "auto_paused": int(auto_paused),
                    "classification_json": classification_json,
                    "created_at": now, "updated_at": now,
                })
            except IntegrityError as exc:
                raise AgentRunConflict("another task follow-up is still pending") from exc
            current = self._locked_run(conn, run_id=run_id, student_id=student_id)
            event_type = (
                "run.followup_classification_waiting"
                if operation_status == "waiting_safe_point" else "run.followup_received"
            )
            self._append_event(
                conn, current, event_type=event_type,
                payload={"idempotency_key_sha256": key_sha256,
                         "request_sha256": request_sha256,
                         "contract_revision": expected_revision,
                         "operation_status": operation_status,
                         "model_required": model_required},
            )
            row = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            return {
                "created": True,
                "operation": self._followup_operation_view(row),
                "run": self._run_view(current),
            }

    def reserve_task_followup_model_call(
        self,
        run_id: str,
        *,
        student_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Reserve one classifier call after a durable safe point and budget read."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key is invalid")
        key_sha256 = self._followup_key_hash(run_id, student_id, idempotency_key)
        usage = self.task_usage_for_owner(run_id, student_id=student_id)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            op = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            if op is None:
                raise AgentRunNotFound("Task follow-up operation not found")
            if int(op["contract_revision"]) != expected_revision:
                raise StaleTaskRevision("follow-up operation revision changed")
            if op["operation_status"] != "waiting_safe_point":
                return {"operation": self._followup_operation_view(op),
                        "run": self._run_view(run), "usage": usage,
                        "turn_token_cap": None, "budget_blocked": False,
                        "classification_claimed": False}
            contract = self._task_contract_for_row(conn, run)
            if contract is None or contract.revision != expected_revision:
                raise StaleTaskRevision("follow-up is based on a stale contract revision")
            if run["status"] == "pause_requested":
                return {"operation": self._followup_operation_view(op),
                        "run": self._run_view(run), "usage": usage,
                        "turn_token_cap": None, "budget_blocked": False,
                        "classification_claimed": False}
            if run["status"] == "needs_reconciliation":
                raise AgentRunConflict("uncertain task must be reconciled before follow-up")
            version = int(run["current_plan_version"] or 0)
            active = None
            if version:
                active = conn.execute(text("""
                    SELECT status FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version
                      AND status IN ('running','needs_reconciliation')
                    ORDER BY step_index LIMIT 1
                """), {"run_id": run_id, "version": version}).mappings().first()
            if active:
                if active["status"] == "needs_reconciliation":
                    raise AgentRunConflict("uncertain step must be reconciled before follow-up")
                return {"operation": self._followup_operation_view(op),
                        "run": self._run_view(run), "usage": usage,
                        "turn_token_cap": None, "budget_blocked": False,
                        "classification_claimed": False}
            if run["status"] not in {
                "paused", "waiting_input", "waiting_authorization",
                "completed", "failed", "cancelled", "partial",
            }:
                return {"operation": self._followup_operation_view(op),
                        "run": self._run_view(run), "usage": usage,
                        "turn_token_cap": None, "budget_blocked": False,
                        "classification_claimed": False}

            budget = contract.budget
            budget_reason = None
            if not usage.get("verified"):
                budget_reason = "task_usage_unverified"
            elif (usage.get("model_calls", 0) >= budget.max_model_calls
                  or usage.get("input_tokens", 0) >= budget.max_input_tokens
                  or usage.get("output_tokens", 0) >= budget.max_output_tokens
                  or usage.get("total_tokens", 0) >= budget.max_input_tokens + budget.max_output_tokens):
                budget_reason = "task_budget_exhausted"
            remaining = min(
                budget.max_input_tokens - usage.get("input_tokens", 0)
                + budget.max_output_tokens - usage.get("output_tokens", 0),
                budget.max_input_tokens + budget.max_output_tokens
                - usage.get("total_tokens", 0),
            ) if usage.get("verified") else 0
            if budget_reason or remaining <= 0:
                blocked_action = (
                    "classification_unavailable"
                    if budget_reason == "task_usage_unverified"
                    else "budget_exhausted"
                )
                result_json, _ = _canonical_json({
                    "action": blocked_action,
                    "reason": budget_reason or "task_budget_exhausted",
                }, max_bytes=2048)
                now = _utcnow()
                conn.execute(text("""
                    UPDATE agent_run_task_followup SET operation_status='completed',
                        model_required=0, active_slot=NULL, result_json=:result_json,
                        updated_at=:now
                    WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
                """), {"run_id": run_id, "key_sha256": key_sha256,
                      "result_json": result_json, "now": now})
                row = conn.execute(text("""
                    SELECT * FROM agent_run_task_followup
                    WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
                """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
                return {"operation": self._followup_operation_view(row),
                        "run": self._run_view(run), "usage": usage,
                        "turn_token_cap": None, "budget_blocked": True,
                        "classification_claimed": False}

            turn_token_cap = min(
                remaining,
                budget.max_call_input_tokens + budget.max_call_output_tokens,
            )
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run_task_followup SET operation_status='classifying',
                    reserved_model_calls=1, updated_at=:now
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256, "now": now})
            self._append_event(
                conn, run, event_type="run.followup_classification_started",
                payload={"idempotency_key_sha256": key_sha256,
                         "contract_revision": expected_revision,
                         "reserved_model_calls": 1},
            )
            row = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            return {"operation": self._followup_operation_view(row),
                    "run": self._run_view(run), "usage": usage,
                    "turn_token_cap": turn_token_cap, "budget_blocked": False,
                    "classification_claimed": True}

    def store_task_followup_classification(
        self,
        run_id: str,
        *,
        student_id: str,
        idempotency_key: str,
        classification: Mapping[str, Any],
        model_usage: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Persist validated classifier output and billable usage before applying it."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key is invalid")
        classification_json, _ = _canonical_json(dict(classification), max_bytes=8192)
        model_usage_json, _ = _canonical_json(dict(model_usage), max_bytes=16 * 1024)
        key_sha256 = self._followup_key_hash(run_id, student_id, idempotency_key)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            row = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            if row is None:
                raise AgentRunNotFound("Task follow-up operation not found")
            if row["operation_status"] in {"classified", "completed"}:
                return {"operation": self._followup_operation_view(row),
                        "run": self._run_view(run)}
            if row["operation_status"] != "classifying":
                raise AgentRunConflict("follow-up classifier reservation is no longer active")
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run_task_followup SET operation_status='classified',
                    reserved_model_calls=0, classification_json=:classification_json,
                    model_usage_json=:model_usage_json, updated_at=:now
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {
                "run_id": run_id, "key_sha256": key_sha256,
                "classification_json": classification_json,
                "model_usage_json": model_usage_json, "now": now,
            })
            self._append_event(
                conn, run, event_type="run.followup_classified",
                payload={"idempotency_key_sha256": key_sha256,
                         "contract_revision": int(row["contract_revision"]),
                         "intent": dict(classification).get("intent"),
                         "confidence": dict(classification).get("confidence")},
            )
            updated = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            return {"operation": self._followup_operation_view(updated),
                    "run": self._run_view(run)}

    def mark_task_followup_uncertain(
        self,
        run_id: str,
        *,
        student_id: str,
        idempotency_key: str,
        model_usage: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fence an interrupted classifier call instead of issuing it again."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key is invalid")
        usage_json = None
        if model_usage is not None:
            usage_json, _ = _canonical_json(dict(model_usage), max_bytes=16 * 1024)
        key_sha256 = self._followup_key_hash(run_id, student_id, idempotency_key)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            row = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            if row is None:
                raise AgentRunNotFound("Task follow-up operation not found")
            if row["operation_status"] == "completed":
                return {"operation": self._followup_operation_view(row),
                        "run": self._run_view(run)}
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run_task_followup SET operation_status='needs_reconciliation',
                    reserved_model_calls=CASE WHEN reserved_model_calls > 0 THEN reserved_model_calls ELSE 1 END,
                    model_usage_json=COALESCE(:usage_json, model_usage_json), updated_at=:now
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256,
                  "usage_json": usage_json, "now": now})
            self._append_event(
                conn, run, event_type="run.followup_needs_reconciliation",
                payload={"idempotency_key_sha256": key_sha256,
                         "contract_revision": int(row["contract_revision"]),
                         "reason": "classifier_outcome_unknown"},
            )
            updated = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            return {"operation": self._followup_operation_view(updated),
                    "run": self._run_view(run)}

    def block_task_followup_operation(
        self, run_id: str, *, student_id: str, idempotency_key: str,
        result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Settle a classifier rejected by a known pre-dispatch budget gate."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key is invalid")
        result_json, _ = _canonical_json(dict(result), max_bytes=8192)
        key_sha256 = self._followup_key_hash(run_id, student_id, idempotency_key)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            row = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            if row is None:
                raise AgentRunNotFound("Task follow-up operation not found")
            if row["operation_status"] == "completed":
                return {"operation": self._followup_operation_view(row),
                        "run": self._run_view(run)}
            if row["operation_status"] != "classifying":
                raise AgentRunConflict("classifier is not waiting at a pre-dispatch gate")
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run_task_followup SET operation_status='completed',
                    model_required=0, reserved_model_calls=0, active_slot=NULL,
                    result_json=:result_json, updated_at=:now
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256,
                  "result_json": result_json, "now": now})
            self._append_event(
                conn, run, event_type="run.followup_budget_blocked",
                payload={"idempotency_key_sha256": key_sha256,
                         "contract_revision": int(row["contract_revision"]),
                         "reason": dict(result).get("reason", "budget_blocked")},
            )
            updated = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            return {"operation": self._followup_operation_view(updated),
                    "run": self._run_view(run)}

    def complete_task_followup_operation(
        self,
        run_id: str,
        *,
        student_id: str,
        idempotency_key: str,
        result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key is invalid")
        result_json, _ = _canonical_json(dict(result), max_bytes=8192)
        key_sha256 = self._followup_key_hash(run_id, student_id, idempotency_key)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            row = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            if row is None:
                raise AgentRunNotFound("Task follow-up operation not found")
            if row["operation_status"] == "completed":
                if row["result_json"] is None:
                    raise AgentRunStoreError("Completed follow-up has no persisted result")
                previous = _decode_json(row["result_json"], expected_type=dict,
                                        field="task follow-up result")
                if previous != dict(result):
                    raise AgentRunIdempotencyConflict(
                        "Follow-up retry produced a different control result"
                    )
                return {"operation": self._followup_operation_view(row),
                        "run": self._run_view(run)}
            if row["operation_status"] not in {"classified", "classified_quick"}:
                raise AgentRunConflict("follow-up classification is not ready to apply")
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run_task_followup SET operation_status='completed',
                    active_slot=NULL, result_json=:result_json, updated_at=:now
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256,
                  "result_json": result_json, "now": now})
            self._append_event(
                conn, run, event_type="run.followup_applied",
                payload={"idempotency_key_sha256": key_sha256,
                         "contract_revision": int(row["contract_revision"]),
                         "action": dict(result).get("action")},
            )
            updated = conn.execute(text("""
                SELECT * FROM agent_run_task_followup
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            return {"operation": self._followup_operation_view(updated),
                    "run": self._run_view(run)}

    def get_task_followup_operation(
        self, run_id: str, *, student_id: str, idempotency_key: str,
    ) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key is invalid")
        key_sha256 = self._followup_key_hash(run_id, student_id, idempotency_key)
        with self._read_connection() as conn:
            with conn.begin():
                run = self._select_run(conn, run_id=run_id, student_id=student_id)
                if run is None:
                    raise AgentRunNotFound("Agent run not found")
                row = conn.execute(text("""
                    SELECT * FROM agent_run_task_followup
                    WHERE run_id=:run_id AND student_id=:student_id
                      AND idempotency_key_sha256=:key_sha256
                """), {"run_id": run_id, "student_id": student_id,
                      "key_sha256": key_sha256}).mappings().first()
                if row is None:
                    raise AgentRunNotFound("Task follow-up operation not found")
                return {"operation": self._followup_operation_view(row),
                        "run": self._run_view(run)}

    def request_pause(self, run_id: str, *, student_id: str) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            if run["status"] == "paused":
                already_paused = True
            else:
                already_paused = False
            if already_paused:
                pass
            elif run["status"] in TERMINAL_RUN_STATUSES | {"needs_reconciliation"}:
                raise AgentRunConflict("terminal or uncertain task cannot be paused")
            else:
                version = int(run["current_plan_version"] or 0)
                active = None
                if version:
                    active = conn.execute(text("""
                        SELECT status FROM agent_run_step
                        WHERE run_id=:run_id AND plan_version=:version
                          AND status IN ('running','needs_reconciliation')
                        ORDER BY step_index LIMIT 1
                    """), {"run_id": run_id, "version": version}).mappings().first()
                if active and active["status"] == "needs_reconciliation":
                    raise AgentRunConflict("uncertain step must be reconciled before pausing")
                if run["status"] == "pause_requested":
                    already_paused = True
                else:
                    now = _utcnow()
                    phase_in_flight = run["status"] in {"planning", "finalizing"}
                    waiting_for_step = bool(active and active["status"] == "running")
                    wait_for_safe_point = waiting_for_step or phase_in_flight
                    new_status = "pause_requested" if wait_for_safe_point else "paused"
                    self._append_event(
                        conn, run,
                        event_type="run.pause_requested" if wait_for_safe_point else "run.paused",
                        new_status=new_status,
                        new_pause_requested_at=now if wait_for_safe_point else None,
                        payload={"safe_point_pending": wait_for_safe_point},
                    )
                    if not wait_for_safe_point:
                        conn.execute(text("""
                            UPDATE agent_run_dispatch SET dispatch_state='paused',
                                publish_token=NULL, publish_lease_until=NULL,
                                claim_token=NULL, lease_until=NULL, updated_at=:now
                            WHERE run_id=:run_id
                        """), {"run_id": run_id, "now": now})
        return self.get_for_owner(run_id, student_id=student_id)

    def mark_paused_at_safe_point(
        self, run_id: str, *, student_id: str, claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if run["status"] == "paused":
                already_paused = True
            else:
                already_paused = False
            if already_paused:
                pass
            elif run["status"] != "pause_requested":
                raise AgentRunConflict("no pause request is pending")
            else:
                version = int(run["current_plan_version"] or 0)
                if version:
                    active = conn.execute(text("""
                        SELECT COUNT(*) FROM agent_run_step
                        WHERE run_id=:run_id AND plan_version=:version
                          AND status IN ('running','needs_reconciliation')
                    """), {"run_id": run_id, "version": version}).scalar_one()
                    if int(active or 0):
                        raise AgentRunConflict("active or uncertain step prevents safe pause")
                now = _utcnow()
                self._append_event(
                    conn, run, event_type="run.paused", new_status="paused",
                    new_pause_requested_at=None,
                    payload={"safe_point": True},
                )
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='paused',
                        publish_token=NULL, publish_lease_until=NULL,
                        claim_token=NULL, lease_until=NULL, updated_at=:now
                    WHERE run_id=:run_id
                """), {"run_id": run_id, "now": now})
        return self.get_for_owner(run_id, student_id=student_id)

    def resume_task(self, run_id: str, *, student_id: str, expected_revision: int) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            contract = self._task_contract_for_row(conn, run)
            if contract is None:
                raise AgentRunConflict("task contract has not been initialized")
            if contract.revision != expected_revision:
                raise AgentRunConflict("task contract revision changed before resume")
            if run["status"] != "paused":
                if run["status"] in {"waiting_input", "waiting_authorization"}:
                    raise AgentRunConflict("waiting task must be resolved before resume")
                raise AgentRunConflict("task is not paused")
            now = _utcnow()
            version = int(run["current_plan_version"] or 0)
            resume_status = "queued"
            if run.get("final_result_json") is not None and run.get("finalized_at") is None:
                # A pause requested during final response generation stores a
                # verified draft checkpoint. Resume can commit it without
                # paying for or replaying the model call.
                resume_status = "finalizing"
            elif version:
                plan_row = conn.execute(text("""
                    SELECT plan_json FROM agent_run_plan
                    WHERE run_id=:run_id AND plan_version=:version
                """), {"run_id": run_id, "version": version}).mappings().first()
                if plan_row:
                    plan_json = _decode_json(plan_row["plan_json"], expected_type=dict, field="plan")
                    metadata = plan_json.get("metadata") or {}
                    if metadata.get("task_contract_revision") == contract.revision:
                        incomplete = conn.execute(text("""
                            SELECT COUNT(*) FROM agent_run_step
                            WHERE run_id=:run_id AND plan_version=:version
                              AND status<>'completed'
                        """), {"run_id": run_id, "version": version}).scalar_one()
                        resume_status = "steps_completed" if int(incomplete or 0) == 0 else "planned"
            self._append_event(
                conn, run, event_type="run.resumed", new_status=resume_status,
                new_pause_requested_at=None,
                payload={"contract_revision": contract.revision,
                         "plan_version": version},
            )
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='pending',
                    publish_token=NULL, publish_lease_until=NULL,
                    claim_token=NULL, lease_until=NULL,
                    next_publish_at=:now, updated_at=:now
                WHERE run_id=:run_id
            """), {"run_id": run_id, "now": now})
        return self.get_for_owner(run_id, student_id=student_id)

    def set_waiting_status(
        self, run_id: str, *, student_id: str, status: str, reason: str,
        claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in {"waiting_input", "waiting_authorization"}:
            raise ValueError("unsupported waiting status")
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if run["status"] in TERMINAL_RUN_STATUSES:
                raise AgentRunConflict("terminal task cannot enter a waiting state")
            if run["status"] == status:
                already_waiting = True
            else:
                already_waiting = False
            if already_waiting:
                pass
            else:
                version = int(run["current_plan_version"] or 0)
                if version:
                    active = conn.execute(text("""
                        SELECT COUNT(*) FROM agent_run_step
                        WHERE run_id=:run_id AND plan_version=:version
                          AND status IN ('running','needs_reconciliation')
                    """), {"run_id": run_id, "version": version}).scalar_one()
                    if int(active or 0):
                        raise AgentRunConflict("active or uncertain step prevents waiting state")
                self._append_event(
                    conn, run, event_type=("run.waiting_input" if status == "waiting_input"
                                           else "run.waiting_authorization"),
                    new_status=status, payload={"reason": _safe_event_reason(reason)},
                )
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state=:status,
                        publish_token=NULL, publish_lease_until=NULL,
                        claim_token=NULL, lease_until=NULL, updated_at=:now
                    WHERE run_id=:run_id
                """), {"run_id": run_id, "status": status, "now": _utcnow()})
        return self.get_for_owner(run_id, student_id=student_id)

    def mark_partial(
        self, run_id: str, *, student_id: str, final_result: Mapping[str, Any],
        reason: str, claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        normalized = _normalize_final_result(final_result)
        encoded, digest = _canonical_json(normalized, max_bytes=_MAX_RESULT_BYTES)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if run["status"] == "partial":
                if run.get("final_result_sha256") != digest:
                    raise AgentRunConflict("partial result changed after settlement")
                return self._run_view(run)
            if run["status"] in TERMINAL_RUN_STATUSES and not (
                run["status"] == "failed"
                and run.get("final_result_json") is None
                and reason == "task_budget_exhausted"
            ):
                raise AgentRunConflict("terminal task cannot be settled as partial")
            if run.get("cancel_requested_at") is not None:
                raise AgentRunConflict("cancel is pending; partial completion is not publishable")
            version = int(run["current_plan_version"] or 0)
            if version:
                unsafe = conn.execute(text("""
                    SELECT COUNT(*) FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version
                      AND status IN ('running','needs_reconciliation')
                """), {"run_id": run_id, "version": version}).scalar_one()
                if int(unsafe or 0):
                    raise AgentRunConflict("active or uncertain step prevents partial settlement")
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run SET final_result_json=:result_json,
                    final_result_sha256=:result_sha256, finalized_at=:now
                WHERE run_id=:run_id AND student_id=:student_id
            """), {"result_json": encoded, "result_sha256": digest,
                  "now": now, "run_id": run_id, "student_id": student_id})
            self._append_event(
                conn, run, event_type="run.partial", new_status="partial",
                payload={"reason": _safe_event_reason(reason), "result_sha256": digest},
            )
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='terminal',
                    claim_token=NULL, lease_until=NULL,
                    publish_token=NULL, publish_lease_until=NULL, updated_at=:now
                WHERE run_id=:run_id
            """), {"now": now, "run_id": run_id})
        return self.get_for_owner(run_id, student_id=student_id)

    def confirm_artifact_save(
        self,
        run_id: str,
        *,
        student_id: str,
        contract_revision: int,
        plan_version: int,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Persist a one-shot save grant bound to an exact result revision."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if type(contract_revision) is not int or contract_revision < 1:
            raise ValueError("contract_revision must be positive")
        self._validate_version_index(plan_version, 1)
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key must be 8..128 safe ASCII characters")
        key_sha256 = hashlib.sha256(
            ("cpa-agent-artifact-save\0v1\0" + run_id + "\0" + student_id + "\0" + idempotency_key)
            .encode("utf-8")
        ).hexdigest()

        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            if not self._task_contract_schema_available(conn):
                raise AgentRunStoreError("Agent task control migration 0021 is not applied")
            contract = self._task_contract_for_row(conn, run)
            if contract is None or contract.revision != contract_revision:
                raise AgentRunConflict("task contract revision changed before save confirmation")
            if run["status"] not in {"completed", "partial"} or not run.get("final_result_json"):
                raise AgentRunConflict("only a durable final or partial result can be saved")
            if int(run["current_plan_version"] or 0) != plan_version:
                raise AgentRunConflict("requested plan version is no longer current")
            plan_row = conn.execute(text("""
                SELECT plan_json, plan_sha256 FROM agent_run_plan
                WHERE run_id=:run_id AND plan_version=:version
            """), {"run_id": run_id, "version": plan_version}).mappings().first()
            if not plan_row:
                raise AgentRunNotFound("Agent plan version not found")
            plan_json = _decode_json(plan_row["plan_json"], expected_type=dict, field="plan")
            if (plan_json.get("metadata") or {}).get("task_contract_revision") != contract.revision:
                raise AgentRunConflict("requested plan belongs to an obsolete task contract")
            target = f"agent-run://{run_id}/plan/{plan_version}"
            target_version = str(plan_row["plan_sha256"])
            confirmation = "cfm_" + hashlib.sha256(
                f"{run_id}\0{contract.revision}\0{plan_version}\0artifact.save".encode("utf-8")
            ).hexdigest()[:32]
            artifact_id = "art_" + hashlib.sha256(
                f"{run_id}\0{contract.revision}\0{plan_version}\0artifact.save".encode("utf-8")
            ).hexdigest()[:32]
            grant = WriteGrant(
                action="artifact.save", target=target, target_version=target_version,
                confirmation_id=confirmation, contract_revision=contract.revision,
            )
            authorized_contract = replace(contract, writes=(grant,))
            if not write_is_authorized(
                authorized_contract, action="artifact.save", target=target,
                target_version=target_version,
            ):
                raise AgentRunConflict("save confirmation is not bound to the current result")
            request_record = {
                "action": "artifact.save", "run_id": run_id,
                "student_id": student_id, "contract_revision": contract.revision,
                "plan_version": plan_version, "plan_sha256": target_version,
                "target": target,
            }
            _, request_sha256 = _canonical_json(request_record, max_bytes=4096)
            prior = conn.execute(text("""
                SELECT contract_revision, plan_version, plan_sha256, action, target,
                       target_version, confirmation_id, idempotency_key_sha256,
                       request_sha256, artifact_id, artifact_saved_at
                FROM agent_run_task_write_confirmation
                WHERE run_id=:run_id AND idempotency_key_sha256=:key_sha256
            """), {"run_id": run_id, "key_sha256": key_sha256}).mappings().first()
            if prior:
                if prior["request_sha256"] != request_sha256:
                    raise AgentRunIdempotencyConflict(
                        "Idempotency-Key already identifies a different save confirmation"
                    )
                return self._confirmation_view(prior)
            prior = conn.execute(text("""
                SELECT contract_revision, plan_version, plan_sha256, action, target,
                       target_version, confirmation_id, idempotency_key_sha256,
                       request_sha256, artifact_id, artifact_saved_at
                FROM agent_run_task_write_confirmation
                WHERE run_id=:run_id AND contract_revision=:revision
                  AND plan_version=:version AND action='artifact.save'
            """), {"run_id": run_id, "revision": contract.revision,
                  "version": plan_version}).mappings().first()
            if prior:
                if prior["plan_sha256"] != target_version:
                    raise AgentRunConflict("saved plan digest differs from the requested version")
                return self._confirmation_view(prior)

            conn.execute(text("""
                INSERT INTO agent_run_task_write_confirmation
                (run_id, student_id, contract_revision, plan_version, plan_sha256,
                 action, target, target_version, confirmation_id,
             idempotency_key_sha256, request_sha256, artifact_id, artifact_saved_at,
             created_at)
                VALUES (:run_id, :student_id, :revision, :version, :plan_sha256,
                        'artifact.save', :target, :target_version, :confirmation_id,
                    :key_sha256, :request_sha256, :artifact_id, NULL, :created_at)
            """), {
                "run_id": run_id, "student_id": student_id,
                "revision": contract.revision, "version": plan_version,
                "plan_sha256": target_version, "target": target,
                "target_version": target_version, "confirmation_id": confirmation,
                "key_sha256": key_sha256, "request_sha256": request_sha256,
                "artifact_id": artifact_id, "created_at": _utcnow(),
            })
            self._append_event(
                conn, run, event_type="run.artifact_save_confirmed",
                payload={"confirmation_id": confirmation, "artifact_id": artifact_id,
                         "contract_revision": contract.revision,
                         "plan_version": plan_version, "plan_sha256": target_version},
            )
            row = conn.execute(text("""
                SELECT contract_revision, plan_version, plan_sha256, action, target,
                       target_version, confirmation_id, idempotency_key_sha256,
                       request_sha256, artifact_id, artifact_saved_at
                FROM agent_run_task_write_confirmation
                WHERE run_id=:run_id AND contract_revision=:revision
                  AND plan_version=:version AND action='artifact.save'
            """), {"run_id": run_id, "revision": contract.revision,
                  "version": plan_version}).mappings().first()
            if row is None:
                raise AgentRunStoreError("Artifact save confirmation did not persist")
            return self._confirmation_view(row)

    @staticmethod
    def _confirmation_view(row) -> Dict[str, Any]:
        return {
            "contract_revision": int(row["contract_revision"]),
            "plan_version": int(row["plan_version"]),
            "plan_sha256": str(row["plan_sha256"]),
            "action": str(row["action"]),
            "target": str(row["target"]),
            "target_version": str(row["target_version"]),
            "confirmation_id": str(row["confirmation_id"]),
            "artifact_id": str(row["artifact_id"]),
            "saved": row["artifact_saved_at"] is not None,
            "saved_at": _iso(row["artifact_saved_at"]),
        }

    def mark_artifact_save_verified(
        self, run_id: str, *, student_id: str, confirmation_id: str, artifact_id: str,
    ) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if not isinstance(confirmation_id, str) or not re.fullmatch(r"cfm_[a-f0-9]{32}", confirmation_id):
            raise ValueError("confirmation_id is invalid")
        if not isinstance(artifact_id, str) or not re.fullmatch(r"art_[a-f0-9]{32}", artifact_id):
            raise ValueError("artifact_id is invalid")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            row = conn.execute(text("""
                SELECT contract_revision, plan_version, plan_sha256, action, target,
                       target_version, confirmation_id, idempotency_key_sha256,
                       request_sha256, artifact_id, artifact_saved_at
                FROM agent_run_task_write_confirmation
                WHERE run_id=:run_id AND student_id=:student_id
                  AND confirmation_id=:confirmation_id
            """), {"run_id": run_id, "student_id": student_id,
                  "confirmation_id": confirmation_id}).mappings().first()
            if row is None or row["artifact_id"] != artifact_id:
                raise AgentRunNotFound("Artifact save confirmation not found")
            if row["artifact_saved_at"] is None:
                now = _utcnow()
                conn.execute(text("""
                    UPDATE agent_run_task_write_confirmation SET artifact_saved_at=:now
                    WHERE run_id=:run_id AND confirmation_id=:confirmation_id
                      AND artifact_saved_at IS NULL
                """), {"now": now, "run_id": run_id,
                      "confirmation_id": confirmation_id})
                self._append_event(
                    conn, run, event_type="run.artifact_saved",
                    payload={"confirmation_id": confirmation_id,
                             "artifact_id": artifact_id,
                             "contract_revision": int(row["contract_revision"]),
                             "plan_version": int(row["plan_version"]),
                             "plan_sha256": str(row["plan_sha256"])},
                )
                row = dict(row)
                row["artifact_saved_at"] = now
            return self._confirmation_view(row)

    def submit(
        self,
        *,
        student_id: str,
        session_id: str,
        idempotency_key: str,
        request_payload: Mapping[str, Any],
        principal_id: Optional[str] = None,
        principal_source: Optional[str] = None,
        principal_auth_epoch: Optional[int] = None,
        creation_callback=None,
    ) -> Dict[str, Any]:
        """Create a queued run or recover the prior result for the same key.

        The same owner/session/key plus the same normalized request is
        idempotent. A changed request conflicts. No dispatch occurs here.
        """
        student_id = _owner_value(student_id, field="student_id")
        session_id = _owner_value(session_id, field="session_id")
        idempotency_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("idempotency_key must be 8..128 safe ASCII characters")
        if not isinstance(request_payload, Mapping):
            raise ValueError("request_payload must be a mapping")
        if principal_id is not None:
            principal_id = _owner_value(principal_id, field="principal_id")
        if principal_source is not None:
            principal_source = str(principal_source).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", principal_source):
                raise ValueError("principal_source is invalid")
        if principal_auth_epoch is not None and (
            type(principal_auth_epoch) is not int or principal_auth_epoch < 0
        ):
            raise ValueError("principal_auth_epoch must be a nonnegative integer")
        if principal_source == "account" and (
            principal_id is None or principal_auth_epoch is None
        ):
            raise ValueError("account Principal requires a current auth epoch")
        input_snapshot, encoded_snapshot, request_sha256 = _normalize_input_snapshot(request_payload)
        snapshot_bytes = len(encoded_snapshot.encode("utf-8"))
        key_sha256 = hashlib.sha256(
            ("cpa-agent-run\0v1\0" + student_id + "\0" + session_id + "\0" + idempotency_key)
            .encode("utf-8")
        ).hexdigest()
        run_id = "run_" + uuid.uuid4().hex
        now = _utcnow()
        try:
            with self._utc_transaction() as conn:
                conn.execute(text("""
                    INSERT INTO agent_run
                    (run_id, student_id, session_id, idempotency_key_sha256,
                     request_sha256, input_snapshot_version,
                     input_snapshot_redaction_version, input_snapshot_bytes,
                     input_snapshot_json, input_snapshot_sha256,
                     principal_id, principal_source, principal_auth_epoch,
                     status, current_plan_version, last_event_seq,
                     created_at, updated_at)
                    VALUES (:run_id, :student_id, :session_id, :key_hash,
                            :request_hash, :snapshot_version, :redaction_version,
                            :snapshot_bytes, :snapshot_json, :snapshot_hash,
                            :principal_id, :principal_source, :principal_auth_epoch,
                            'queued', 0, 0, :now, :now)
                """), {
                    "run_id": run_id,
                    "student_id": student_id,
                    "session_id": session_id,
                    "key_hash": key_sha256,
                    "request_hash": request_sha256,
                    "snapshot_version": _INPUT_SNAPSHOT_VERSION,
                    "redaction_version": _INPUT_SNAPSHOT_REDACTION_VERSION,
                    "snapshot_bytes": snapshot_bytes,
                    "snapshot_json": encoded_snapshot,
                    "snapshot_hash": request_sha256,
                    "principal_id": principal_id,
                    "principal_source": principal_source,
                    "principal_auth_epoch": principal_auth_epoch,
                    "now": now,
                })
                row = self._locked_run(conn, run_id=run_id, student_id=student_id)
                contract = None
                if self._task_contract_schema_available(conn):
                    contract = initial_task_contract(
                        run_id, input_snapshot["message"], _DEFAULT_TASK_BUDGET,
                    )
                    self._insert_task_contract_revision(
                        conn, row, contract, mutation_kind="initial",
                        request_sha256=request_sha256,
                    )
                self._append_event(
                    conn,
                    row,
                    event_type="run.submitted",
                    new_current_contract_revision=(contract.revision if contract else None),
                    payload={
                        "request_sha256": request_sha256,
                        **({"contract_revision": contract.revision,
                            "contract_sha256": contract.sha256} if contract else {}),
                    },
                )
                conn.execute(text("""
                    INSERT INTO agent_run_dispatch (run_id, dispatch_state)
                    VALUES (:run_id, 'pending')
                """), {"run_id": run_id})
                if creation_callback is not None:
                    # Internal business adapters bind their owner/revision and
                    # input reference in the same transaction as run/outbox.
                    creation_callback(conn, run_id)
        except IntegrityError as exc:
            existing = self._find_idempotent_run(
                student_id=student_id,
                session_id=session_id,
                key_sha256=key_sha256,
            )
            if existing is None:
                raise AgentRunStoreError("Agent run submission could not be committed") from exc
            if existing["request_sha256"] != request_sha256:
                raise AgentRunIdempotencyConflict(
                    "idempotency key already identifies a different request"
                ) from exc
            return {"created": False, "run": self.get_for_owner(existing["run_id"], student_id=student_id)}

        return {"created": True, "run": self.get_for_owner(run_id, student_id=student_id)}

    def assert_claim(self, run_id, claim_token, conn):
        self._assert_current_claim(conn, run_id, claim_token)

    def record_business_delegation(self, run_id, *, student_id, claim_token, agent):
        if agent not in {'resume_preparation','career_exploration','practice_review','clarify'}:
            raise ValueError('unsupported business delegation')
        with self.mysql.engine.begin() as conn:
            row = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if row['status'] != 'running':
                raise AgentRunConflict('delegation is not in an active step')
            self._append_event(conn, row, event_type='run.business_delegated', payload={'agent':agent,'depth':1})

    def record_resource_read(self, run_id, *, student_id, claim_token, label, phase):
        """Actual bounded read receipts only; never persist prompts or raw data."""
        if phase not in {'started', 'completed'} or label not in {
            '本次探索', '本人经历与自述', '已选方向资料', '本人练习记录与附件', '上传的简历',
        }:
            raise ValueError('unsupported resource receipt')
        with self.mysql.engine.begin() as conn:
            row = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if row['status'] != 'running':
                raise AgentRunConflict('resource read is not in an active step')
            self._append_event(conn, row, event_type='run.resource_read_' + phase,
                               payload={'label': label})

    def begin_planning(
        self, run_id: str, *, student_id: str, claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._utc_transaction() as conn:
            row = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if row["status"] == "planning":
                return self._run_view(row)
            if row["status"] != "queued" or row.get("cancel_requested_at") is not None:
                raise AgentRunConflict("run is not eligible to enter planning")
            self._append_event(
                conn, row, event_type="run.planning", new_status="planning", payload={},
            )
        return self.get_for_owner(run_id, student_id=student_id)

    def persist_plan(
        self,
        run_id: str,
        *,
        student_id: str,
        expected_plan_version: int,
        goal_summary: str,
        steps: Any,
        plan_metadata: Optional[Mapping[str, Any]] = None,
        expected_contract_revision: Optional[int] = None,
        claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Commit one validated immutable plan version and its pending steps.

        ``expected_plan_version`` is a CAS guard. Repeating a successful commit
        with the same expected version and plan digest returns that version;
        changing the plan requires the latest version number and an explicit
        call. A currently running or uncertain step blocks a new version.
        """
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if type(expected_plan_version) is not int or expected_plan_version < 0:
            raise ValueError("expected_plan_version must be a nonnegative integer")
        if expected_contract_revision is not None and (
            type(expected_contract_revision) is not int or expected_contract_revision < 1
        ):
            raise ValueError("expected_contract_revision must be a positive integer")
        goal_summary = str(goal_summary or "").strip()
        if not goal_summary or len(goal_summary) > 1000:
            raise ValueError("goal_summary must contain 1..1000 characters")
        if plan_metadata is not None and not isinstance(plan_metadata, Mapping):
            raise ValueError("plan_metadata must be a mapping")
        capability_binding = (
            plan_metadata.get("capability_binding")
            if isinstance(plan_metadata, Mapping) else None
        )
        capability_id = (
            str(capability_binding.get("capability_id") or "")
            if isinstance(capability_binding, Mapping) else None
        )
        normalized_steps = validate_plan_steps(steps, capability_id=capability_id)
        selected_capability = None
        if capability_id:
            from .expert_capabilities import resolve_explicit_capability

            selected_capability = resolve_explicit_capability({
                "interaction_mode": "experts",
                "expert_id": capability_binding.get("expert_id"),
                "capability_id": capability_id,
            })
            if selected_capability.manifest() != dict(capability_binding):
                raise ValueError("capability_binding_does_not_match_server_manifest")
            selected_capability.validate_plan(normalized_steps)
        plan_payload = {"goal_summary": goal_summary, "steps": normalized_steps}
        if plan_metadata:
            plan_payload["metadata"] = dict(plan_metadata)
        encoded_plan, plan_sha256 = _canonical_json(plan_payload, max_bytes=16 * 1024)

        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            contract = self._task_contract_for_row(conn, run)
            if contract is not None:
                if expected_contract_revision != contract.revision:
                    raise AgentRunConflict("task contract revision changed during planning")
                metadata = plan_payload.get("metadata") or {}
                if metadata.get("task_contract_revision") != contract.revision:
                    raise ValueError("plan metadata must bind the current task contract revision")
            from .expert_capabilities import resolve_explicit_capability

            input_capability = resolve_explicit_capability(
                self._verified_input_snapshot(run),
            )
            if (input_capability.manifest() if input_capability else None) != (
                selected_capability.manifest() if selected_capability else None
            ):
                raise AgentRunConflict("plan capability does not match submitted run selection")
            current = int(run["current_plan_version"] or 0)
            if current == expected_plan_version + 1:
                existing = conn.execute(text("""
                    SELECT plan_sha256 FROM agent_run_plan
                    WHERE run_id=:run_id AND plan_version=:version
                """), {"run_id": run_id, "version": current}).mappings().first()
                if existing and existing["plan_sha256"] == plan_sha256:
                    result = self._plan_view(conn, run_id=run_id, plan_version=current)
                    return result
                raise AgentRunConflict("plan version changed after the caller's snapshot")
            if current != expected_plan_version:
                raise AgentRunConflict("expected plan version is stale")
            if run["status"] not in {"queued", "planning", "planned", "running", "pause_requested"}:
                raise AgentRunConflict("run cannot accept a plan in its current state")
            if run.get("cancel_requested_at") is not None:
                raise AgentRunConflict("cancel is pending; no new plan can be committed")
            if current:
                active = conn.execute(text("""
                    SELECT COUNT(*) FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version
                      AND status IN ('running','needs_reconciliation')
                """), {"run_id": run_id, "version": current}).scalar_one()
                if int(active or 0):
                    raise AgentRunConflict("an active or uncertain step must be reconciled first")
                conn.execute(text("""
                    UPDATE agent_run_step SET status='superseded', finished_at=:now
                    WHERE run_id=:run_id AND plan_version=:version AND status='pending'
                """), {"run_id": run_id, "version": current, "now": _utcnow()})

            version = current + 1
            now = _utcnow()
            conn.execute(text("""
                INSERT INTO agent_run_plan
                (run_id, plan_version, goal_summary, plan_json, plan_sha256, created_at)
                VALUES (:run_id, :version, :goal, :plan_json, :plan_sha256, :now)
            """), {
                "run_id": run_id,
                "version": version,
                "goal": goal_summary,
                "plan_json": encoded_plan,
                "plan_sha256": plan_sha256,
                "now": now,
            })
            for index, step in enumerate(normalized_steps, 1):
                skill_ids_json, _ = _canonical_json(step["skill_ids"], max_bytes=2048)
                conn.execute(text("""
                    INSERT INTO agent_run_step
                    (run_id, plan_version, step_index, role, goal, skill_ids_json, status)
                    VALUES (:run_id, :version, :step_index, :role, :goal, :skill_ids_json, 'pending')
                """), {
                    "run_id": run_id,
                    "version": version,
                    "step_index": index,
                    "role": step["role"],
                    "goal": step["goal"],
                    "skill_ids_json": skill_ids_json,
                })
            self._append_event(
                conn,
                run,
                event_type="run.plan_created" if version == 1 else "run.plan_revised",
                new_status=("pause_requested" if run["status"] == "pause_requested" else "planned"),
                new_current_plan_version=version,
                payload={
                    "plan_version": version,
                    "step_count": len(normalized_steps),
                    "plan_sha256": plan_sha256,
                },
            )
            result = self._plan_view(conn, run_id=run_id, plan_version=version)
        return result

    def start_step(
        self,
        run_id: str,
        *,
        student_id: str,
        plan_version: int,
        step_index: int,
        claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark a pending step running once; running/uncertain steps never auto-retry."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        self._validate_version_index(plan_version, step_index)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if run["status"] not in {"planned", "running"}:
                raise AgentRunConflict("run is not ready to start a step")
            if int(run["current_plan_version"]) != plan_version:
                raise AgentRunConflict("step belongs to a stale plan version")
            if run.get("cancel_requested_at") is not None:
                raise AgentRunConflict("cancel is pending; no subsequent step may start")
            step = self._locked_step(conn, run_id, plan_version, step_index)
            if step["status"] != "pending":
                raise AgentRunConflict(
                    "step is not pending; uncertain or started effects are never automatically rerun"
                )
            if step_index > 1:
                prior = conn.execute(text("""
                    SELECT COUNT(*) FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version
                      AND step_index<:step_index AND status<>'completed'
                """), {
                    "run_id": run_id,
                    "version": plan_version,
                    "step_index": step_index,
                }).scalar_one()
                if int(prior or 0):
                    raise AgentRunConflict("serial plan steps must complete in order")
            attempt = int(step["attempts"] or 0) + 1
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run_step SET status='running', attempts=:attempt,
                    started_at=:now, finished_at=NULL, error_code=NULL
                WHERE run_id=:run_id AND plan_version=:version AND step_index=:step_index
                  AND status='pending'
            """), {
                "attempt": attempt,
                "now": now,
                "run_id": run_id,
                "version": plan_version,
                "step_index": step_index,
            })
            self._append_event(
                conn,
                run,
                event_type="run.step_started",
                new_status="running",
                payload={
                    "plan_version": plan_version,
                    "step_index": step_index,
                    "role": step["role"],
                    "attempt": attempt,
                },
            )
        return self.get_for_owner(run_id, student_id=student_id)

    def finish_step(
        self,
        run_id: str,
        *,
        student_id: str,
        plan_version: int,
        step_index: int,
        outcome: str,
        result: Optional[Mapping[str, Any]] = None,
        error_code: Optional[str] = None,
        expected_contract_revision: Optional[int] = None,
        claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Settle a started step; ambiguity is durable and blocks all retries."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        self._validate_version_index(plan_version, step_index)
        if not isinstance(outcome, str) or outcome not in {"completed", "failed", "needs_reconciliation"}:
            raise ValueError("outcome must be completed, failed, or needs_reconciliation")
        if result is not None and not isinstance(result, Mapping):
            raise ValueError("step result must be a mapping")
        encoded_result = None
        result_sha256 = None
        if result is not None:
            encoded_result, result_sha256 = _canonical_json(dict(result), max_bytes=_MAX_RESULT_BYTES)
        error_code = str(error_code or "").strip() or None
        if error_code and not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("error_code contains unsupported characters")

        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if int(run["current_plan_version"]) != plan_version:
                raise AgentRunConflict("step belongs to a stale plan version")
            step = self._locked_step(conn, run_id, plan_version, step_index)
            contract = self._task_contract_for_row(conn, run)
            if contract is not None and expected_contract_revision is None:
                raise AgentRunConflict("step settlement must bind the task contract revision")
            if contract is not None and expected_contract_revision != contract.revision:
                if step["status"] != "running":
                    raise AgentRunConflict("stale output has no active step to fence")
                if step["role"] != "qa":
                    raise AgentRunConflict("stale write output requires reconciliation")
                now = _utcnow()
                stale_usage = {}
                if isinstance(result, Mapping):
                    if isinstance(result.get("model_usage"), Mapping):
                        stale_usage["model_usage"] = dict(result["model_usage"])
                    tool_call_count = result.get("tool_call_count")
                    tool_calls = result.get("tool_calls")
                    if type(tool_call_count) is int and tool_call_count >= 0:
                        stale_usage["tool_call_count"] = min(tool_call_count, 1_000_000)
                    elif isinstance(tool_calls, list):
                        stale_usage["tool_call_count"] = len(tool_calls)
                stale_encoded, stale_digest = _canonical_json(
                    {"stale_output_discarded": True, **stale_usage}, max_bytes=_MAX_RESULT_BYTES,
                )
                conn.execute(text("""
                    UPDATE agent_run_step SET status='superseded', result_json=:result_json,
                        result_sha256=:result_sha256, error_code='task_contract_superseded', finished_at=:now
                    WHERE run_id=:run_id AND plan_version=:version AND step_index=:step_index
                      AND status='running'
                """), {"now": now, "result_json": stale_encoded,
                      "result_sha256": stale_digest, "run_id": run_id, "version": plan_version,
                      "step_index": step_index})
                conn.execute(text("""
                    UPDATE agent_run_step SET status='superseded', finished_at=:now
                    WHERE run_id=:run_id AND plan_version=:version AND status='pending'
                """), {"now": now, "run_id": run_id, "version": plan_version})
                self._append_event(
                    conn, run, event_type="run.step_superseded", new_status="queued",
                    new_pause_requested_at=None,
                    payload={"plan_version": plan_version, "step_index": step_index,
                             "contract_revision": contract.revision},
                )
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='pending',
                        claim_token=NULL, lease_until=NULL,
                        publish_token=NULL, publish_lease_until=NULL,
                        next_publish_at=:now, updated_at=:now
                    WHERE run_id=:run_id AND claim_token=:claim_token
                """), {"run_id": run_id, "claim_token": claim_token, "now": now})
                view = self._run_view(run)
                view["status"] = "queued"
                view["last_event_seq"] = int(run["last_event_seq"] or 0) + 1
                view["pause_requested"] = False
                view["current_contract_revision"] = contract.revision
                view["task_contract"] = task_contract_to_record(contract)
                view["current_plan"] = self._plan_view(
                    conn, run_id=run_id, plan_version=plan_version,
                )
                return view
            if step["status"] == outcome:
                if step.get("result_sha256") == result_sha256 and step.get("error_code") == error_code:
                    view = self._run_view(run)
                    view["current_plan"] = self._plan_view(
                        conn, run_id=run_id, plan_version=int(run["current_plan_version"]),
                    )
                    return view
                raise AgentRunConflict("step already settled with a different result")
            if run["status"] not in {"running", "pause_requested"} or step["status"] != "running":
                raise AgentRunConflict("only the currently running step can be settled")

            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run_step SET status=:status, result_json=:result_json,
                    result_sha256=:result_sha256, error_code=:error_code, finished_at=:now
                WHERE run_id=:run_id AND plan_version=:version AND step_index=:step_index
                  AND status='running'
            """), {
                "status": outcome,
                "result_json": encoded_result,
                "result_sha256": result_sha256,
                "error_code": error_code,
                "now": now,
                "run_id": run_id,
                "version": plan_version,
                "step_index": step_index,
            })
            if outcome == "needs_reconciliation":
                new_status = "needs_reconciliation"
                event_type = "run.step_needs_reconciliation"
            elif outcome == "failed":
                new_status = "failed"
                event_type = "run.step_failed"
            else:
                pending = conn.execute(text("""
                    SELECT COUNT(*) FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version AND status<>'completed'
                """), {"run_id": run_id, "version": plan_version}).scalar_one()
                all_steps_completed = int(pending or 0) == 0
                if run["status"] == "pause_requested":
                    new_status = "paused"
                else:
                    new_status = "steps_completed" if all_steps_completed else "running"
                event_type = "run.steps_completed" if all_steps_completed else "run.step_completed"
            self._append_event(
                conn,
                run,
                event_type=event_type,
                new_status=new_status,
                new_pause_requested_at=None if new_status == "paused" else ...,
                payload={
                    "plan_version": plan_version,
                    "step_index": step_index,
                    "role": step["role"],
                    "step_status": outcome,
                    "result_sha256": result_sha256,
                    "error_code": error_code,
                },
            )
            if new_status == "paused":
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='paused',
                        claim_token=NULL, lease_until=NULL,
                        publish_token=NULL, publish_lease_until=NULL, updated_at=:now
                    WHERE run_id=:run_id AND claim_token=:claim_token
                """), {"run_id": run_id, "claim_token": claim_token, "now": _utcnow()})
        return self.get_for_owner(run_id, student_id=student_id)

    def begin_finalizing(
        self, run_id: str, *, student_id: str, claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark that all planned steps are complete and final response generation begins."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if run["status"] == "finalizing":
                return self._run_view(run)
            if run["status"] != "steps_completed":
                raise AgentRunConflict("all plan steps must complete before finalization")
            if run.get("cancel_requested_at") is not None:
                raise AgentRunConflict("cancel is pending; final response must not be generated")
            version = int(run["current_plan_version"] or 0)
            if version < 1:
                raise AgentRunConflict("run has no completed plan")
            incomplete = conn.execute(text("""
                SELECT COUNT(*) FROM agent_run_step
                WHERE run_id=:run_id AND plan_version=:version AND status<>'completed'
            """), {"run_id": run_id, "version": version}).scalar_one()
            if int(incomplete or 0):
                raise AgentRunConflict("plan steps are not all completed")
            self._append_event(
                conn, run, event_type="run.finalizing", new_status="finalizing", payload={},
            )
        return self.get_for_owner(run_id, student_id=student_id)

    def complete_run(
        self,
        run_id: str,
        *,
        student_id: str,
        final_result: Mapping[str, Any],
        expected_contract_revision: Optional[int] = None,
        claim_token: Optional[str] = None,
        completion_callback=None,
    ) -> Dict[str, Any]:
        """Atomically persist the final reply/reference and the completed event.

        ``completed`` means a user-facing result is durable, not merely that
        the last sub-agent step returned. A future route may expose this result
        only after an owner-bound read.
        """
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if not isinstance(final_result, Mapping):
            raise ValueError("final_result must be a mapping")
        normalized_result = _normalize_final_result(final_result)
        encoded_result, result_sha256 = _canonical_json(
            normalized_result, max_bytes=_MAX_RESULT_BYTES,
        )
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            contract = self._task_contract_for_row(conn, run)
            if contract is not None and expected_contract_revision is None:
                raise AgentRunConflict("final settlement must bind the task contract revision")
            if contract is not None and expected_contract_revision != contract.revision:
                now = _utcnow()
                self._append_event(
                    conn, run, event_type="run.finalization_superseded",
                    new_status="queued", new_pause_requested_at=None,
                    payload={"expected_contract_revision": expected_contract_revision,
                             "current_contract_revision": contract.revision},
                )
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='pending',
                        claim_token=NULL, lease_until=NULL,
                        publish_token=NULL, publish_lease_until=NULL,
                        next_publish_at=:now, updated_at=:now
                    WHERE run_id=:run_id AND claim_token=:claim_token
                """), {"run_id": run_id, "claim_token": claim_token, "now": now})
                view = self._run_view(run)
                view["status"] = "queued"
                view["last_event_seq"] = int(run["last_event_seq"] or 0) + 1
                view["current_contract_revision"] = contract.revision
                view["pause_requested"] = False
                view["task_contract"] = task_contract_to_record(contract)
                version = int(run["current_plan_version"] or 0)
                view["current_plan"] = self._plan_view(
                    conn, run_id=run_id, plan_version=version,
                ) if version else None
                return view
            if run["status"] == "completed":
                if run.get("final_result_sha256") != result_sha256:
                    raise AgentRunConflict("run already completed with a different final result")
                view = self._run_view(run)
                version = int(run["current_plan_version"] or 0)
                view["current_plan"] = self._plan_view(
                    conn, run_id=run_id, plan_version=version,
                ) if version else None
                return view
            if (run["status"] != "finalizing" and not (
                run["status"] == "pause_requested"
                and run.get("pause_requested_at") is not None
            )):
                raise AgentRunConflict("run must be finalizing before it can complete")
            if run.get("cancel_requested_at") is not None:
                raise AgentRunConflict("cancel is pending; run cannot be completed")
            version = int(run["current_plan_version"] or 0)
            if version < 1:
                raise AgentRunConflict("run has no completed plan")
            incomplete = conn.execute(text("""
                SELECT COUNT(*) FROM agent_run_step
                WHERE run_id=:run_id AND plan_version=:version AND status<>'completed'
            """), {"run_id": run_id, "version": version}).scalar_one()
            if int(incomplete or 0):
                raise AgentRunConflict("run contains an incomplete plan step")
            if run.get("pause_requested_at") is not None or run["status"] == "pause_requested":
                # The final model result is a local, verified checkpoint. Keep
                # it private in the paused run so resume need not repeat the
                # call; do not run any completion callback while paused.
                now = _utcnow()
                conn.execute(text("""
                    UPDATE agent_run SET final_result_json=:result_json,
                        final_result_sha256=:result_sha256, finalized_at=NULL
                    WHERE run_id=:run_id AND student_id=:student_id
                """), {
                    "result_json": encoded_result, "result_sha256": result_sha256,
                    "run_id": run_id, "student_id": student_id,
                })
                self._append_event(
                    conn, run, event_type="run.paused",
                    new_status="paused", new_pause_requested_at=None,
                    payload={"safe_point": True, "final_result_checkpoint": True,
                             "result_sha256": result_sha256},
                )
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='paused',
                        claim_token=NULL, lease_until=NULL, publish_token=NULL,
                        publish_lease_until=NULL, updated_at=:now
                    WHERE run_id=:run_id
                """), {"run_id": run_id, "now": now})
                view = self._run_view(run)
                view.update({
                    "status": "paused",
                    "last_event_seq": int(run["last_event_seq"] or 0) + 1,
                    "pause_requested": False,
                    "final_result": normalized_result,
                    "final_result_sha256": result_sha256,
                    "finalized_at": None,
                    "final_result_checkpoint": True,
                })
                view["current_plan"] = self._plan_view(
                    conn, run_id=run_id, plan_version=version,
                )
                view["task_contract"] = (
                    task_contract_to_record(contract) if contract is not None else None
                )
                view["saved_artifacts"] = self._saved_artifacts_for_row(conn, run)
                return view
            if completion_callback is not None:
                completion_callback(conn)
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run SET final_result_json=:result_json,
                    final_result_sha256=:result_sha256, finalized_at=:finalized_at
                WHERE run_id=:run_id AND student_id=:student_id
            """), {
                "result_json": encoded_result,
                "result_sha256": result_sha256,
                "finalized_at": now,
                "run_id": run_id,
                "student_id": student_id,
            })
            self._append_event(
                conn,
                run,
                event_type="run.completed",
                new_status="completed",
                payload={
                    "result_ref": f"agent-run://{run_id}/final",
                    "result_sha256": result_sha256,
                },
            )
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='terminal',
                    claim_token=NULL, lease_until=NULL, publish_token=NULL,
                    publish_lease_until=NULL, updated_at=:now
                WHERE run_id=:run_id
            """), {"now": now, "run_id": run_id})
        return self.get_for_owner(run_id, student_id=student_id)

    def complete_direct_run(
        self,
        run_id: str,
        *,
        student_id: str,
        final_result: Mapping[str, Any],
        expected_contract_revision: Optional[int] = None,
        claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a simple direct answer without fabricating a multi-step plan."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if not isinstance(final_result, Mapping):
            raise ValueError("final_result must be a mapping")
        normalized = _normalize_final_result(final_result)
        encoded, digest = _canonical_json(normalized, max_bytes=_MAX_RESULT_BYTES)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            contract = self._task_contract_for_row(conn, run)
            if contract is not None and expected_contract_revision is None:
                raise AgentRunConflict("direct answer must bind the task contract revision")
            if contract is not None and expected_contract_revision != contract.revision:
                now = _utcnow()
                self._append_event(
                    conn, run, event_type="run.finalization_superseded",
                    new_status="queued", new_pause_requested_at=None,
                    payload={"expected_contract_revision": expected_contract_revision,
                             "current_contract_revision": contract.revision},
                )
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='pending',
                        claim_token=NULL, lease_until=NULL, next_publish_at=:now,
                        updated_at=:now WHERE run_id=:run_id AND claim_token=:claim_token
                """), {"run_id": run_id, "claim_token": claim_token, "now": now})
                view = self._run_view(run)
                view.update({"status": "queued",
                             "last_event_seq": int(run["last_event_seq"] or 0) + 1,
                             "current_contract_revision": contract.revision,
                             "task_contract": task_contract_to_record(contract)})
                return view
            if run["status"] == "completed":
                if run.get("final_result_sha256") != digest:
                    raise AgentRunConflict("run already completed with a different direct result")
                return self.get_for_owner(run_id, student_id=student_id)
            if int(run["current_plan_version"] or 0) != 0:
                raise AgentRunConflict("direct answer cannot replace a versioned task plan")
            checkpoint_resume = bool(
                run["status"] == "finalizing"
                and run.get("final_result_json") is not None
                and run.get("finalized_at") is None
            )
            if run["status"] not in {"planning", "pause_requested"} and not checkpoint_resume:
                raise AgentRunConflict("direct answer requires an active planning phase")
            if run.get("cancel_requested_at") is not None:
                raise AgentRunConflict("cancel is pending; direct answer cannot be completed")
            if run.get("pause_requested_at") is not None or run["status"] == "pause_requested":
                now = _utcnow()
                conn.execute(text("""
                    UPDATE agent_run SET final_result_json=:result_json,
                        final_result_sha256=:result_sha256, finalized_at=NULL
                    WHERE run_id=:run_id AND student_id=:student_id
                """), {"result_json": encoded, "result_sha256": digest,
                      "run_id": run_id, "student_id": student_id})
                self._append_event(
                    conn, run, event_type="run.paused", new_status="paused",
                    new_pause_requested_at=None,
                    payload={"safe_point": True, "final_result_checkpoint": True,
                             "direct_answer": True, "result_sha256": digest},
                )
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='paused',
                        claim_token=NULL, lease_until=NULL, publish_token=NULL,
                        publish_lease_until=NULL, updated_at=:now WHERE run_id=:run_id
                """), {"run_id": run_id, "now": now})
                view = self._run_view(run)
                view.update({
                    "status": "paused",
                    "last_event_seq": int(run["last_event_seq"] or 0) + 1,
                    "pause_requested": False,
                    "final_result": normalized,
                    "final_result_sha256": digest,
                    "finalized_at": None,
                    "final_result_checkpoint": True,
                    "task_contract": task_contract_to_record(contract) if contract else None,
                    "saved_artifacts": self._saved_artifacts_for_row(conn, run),
                })
                return view
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run SET final_result_json=:result_json,
                    final_result_sha256=:result_sha256, finalized_at=:now
                WHERE run_id=:run_id AND student_id=:student_id
            """), {"result_json": encoded, "result_sha256": digest,
                  "now": now, "run_id": run_id, "student_id": student_id})
            self._append_event(
                conn, run, event_type="run.completed", new_status="completed",
                payload={"result_ref": f"agent-run://{run_id}/final",
                         "result_sha256": digest, "direct_answer": True},
            )
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='terminal',
                    claim_token=NULL, lease_until=NULL, publish_token=NULL,
                    publish_lease_until=NULL, updated_at=:now WHERE run_id=:run_id
            """), {"run_id": run_id, "now": now})
        return self.get_for_owner(run_id, student_id=student_id)

    def fail_run(
        self,
        run_id: str,
        *,
        student_id: str,
        reply: str,
        error_code: str,
        claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a user-readable, non-success terminal result atomically."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if not isinstance(error_code, str) or not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("error_code is invalid")
        normalized = _normalize_final_result({"reply": reply, "format": "text"})
        encoded, digest = _canonical_json(normalized, max_bytes=_MAX_RESULT_BYTES)
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if run["status"] in TERMINAL_RUN_STATUSES:
                # finish_step(failed) records the truthful step outcome first.
                # The executor then attaches a user-readable terminal reply in
                # a separate transaction. Permit that single settlement only
                # when the run is failed but has no result yet; retries after
                # the reply is committed are idempotent.
                if (run["status"] != "failed"
                        or run.get("final_result_json") is not None):
                    return self._run_view(run)
            if run["status"] == "needs_reconciliation":
                raise AgentRunConflict("uncertain effects cannot be settled as failed")
            version = int(run["current_plan_version"] or 0)
            if version:
                unsafe = conn.execute(text("""
                    SELECT COUNT(*) FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version
                      AND status IN ('running','needs_reconciliation')
                """), {"run_id": run_id, "version": version}).scalar_one()
                if int(unsafe or 0):
                    raise AgentRunConflict("active or uncertain step prevents failed settlement")
            now = _utcnow()
            conn.execute(text("""
                UPDATE agent_run SET final_result_json=:result_json,
                    final_result_sha256=:result_sha256, finalized_at=:now
                WHERE run_id=:run_id AND student_id=:student_id
            """), {"result_json": encoded, "result_sha256": digest,
                  "now": now, "run_id": run_id, "student_id": student_id})
            self._append_event(
                conn, run, event_type="run.failed", new_status="failed",
                payload={"error_code": error_code, "result_sha256": digest},
            )
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='terminal',
                    claim_token=NULL, lease_until=NULL, publish_token=NULL,
                    publish_lease_until=NULL, last_error_code=:error_code,
                    updated_at=:now
                WHERE run_id=:run_id
            """), {"error_code": error_code, "now": now, "run_id": run_id})
        return self.get_for_owner(run_id, student_id=student_id)

    def request_cancel(self, run_id: str, *, student_id: str) -> Dict[str, Any]:
        """Persist a cooperative cancel request; this does not kill/undo work."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            if run["status"] == "cancelled" and run.get("cancel_requested_at") is not None:
                return self._run_view(run)
            if run["status"] in TERMINAL_RUN_STATUSES:
                raise AgentRunConflict("terminal run cannot receive a cancel request")
            if run.get("cancel_requested_at") is None:
                self._append_event(
                    conn,
                    run,
                    event_type="run.cancel_requested",
                    payload={"cancel_requested": True},
                    new_cancel_requested_at=_utcnow(),
                )
        return self.get_for_owner(run_id, student_id=student_id)

    def mark_cancelled_at_safe_point(
        self, run_id: str, *, student_id: str, claim_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """A future executor may acknowledge cancellation only with no uncertain effects."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._utc_transaction() as conn:
            run = self._locked_run(conn, run_id=run_id, student_id=student_id)
            self._assert_current_claim(conn, run_id, claim_token)
            if run["status"] == "cancelled":
                return self._run_view(run)
            if run["status"] in TERMINAL_RUN_STATUSES:
                raise AgentRunConflict("terminal run cannot be changed to cancelled")
            if run.get("cancel_requested_at") is None:
                raise AgentRunConflict("no cancel request is pending")
            if run["status"] == "needs_reconciliation":
                raise AgentRunConflict("uncertain effects must be reconciled before cancellation")
            version = int(run["current_plan_version"] or 0)
            if version:
                unsafe = conn.execute(text("""
                    SELECT COUNT(*) FROM agent_run_step
                    WHERE run_id=:run_id AND plan_version=:version
                      AND status IN ('running','needs_reconciliation')
                """), {"run_id": run_id, "version": version}).scalar_one()
                if int(unsafe or 0):
                    raise AgentRunConflict("an active or uncertain step prevents safe cancellation")
                conn.execute(text("""
                    UPDATE agent_run_step SET status='cancelled', finished_at=:now
                    WHERE run_id=:run_id AND plan_version=:version AND status='pending'
                """), {"run_id": run_id, "version": version, "now": _utcnow()})
            self._append_event(
                conn,
                run,
                event_type="run.cancelled",
                new_status="cancelled",
                payload={"safe_point": True},
            )
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='cancelled',
                    publish_token=NULL, publish_lease_until=NULL,
                    claim_token=NULL, lease_until=NULL
                WHERE run_id=:run_id
            """), {"run_id": run_id})
        return self.get_for_owner(run_id, student_id=student_id)

    def get_for_owner(self, run_id: str, *, student_id: str) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._read_connection() as conn:
            with conn.begin():
                run = self._select_run(conn, run_id=run_id, student_id=student_id)
                if run is None:
                    raise AgentRunNotFound("Agent run not found")
                view = self._run_view(run)
                contract = self._task_contract_for_row(conn, run)
                view["task_contract"] = task_contract_to_record(contract) if contract else None
                view["saved_artifacts"] = self._saved_artifacts_for_row(conn, run)
                version = int(run["current_plan_version"] or 0)
                view["current_plan"] = (
                    self._plan_view(conn, run_id=run_id, plan_version=version)
                    if version else None
                )
                return view

    def get_input_snapshot_for_owner(self, run_id: str, *, student_id: str) -> Dict[str, Any]:
        """Read the durable input only for the owner, separately from status/event views."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._read_connection() as conn:
            with conn.begin():
                run = self._select_run(conn, run_id=run_id, student_id=student_id)
                if run is None:
                    raise AgentRunNotFound("Agent run not found")
                return self._verified_input_snapshot(run)

    def get_auth_binding_for_owner(self, run_id: str, *, student_id: str) -> Dict[str, Any]:
        """Read the account identity and auth epoch captured at submit time."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._read_connection() as conn:
            with conn.begin():
                run = self._select_run(conn, run_id=run_id, student_id=student_id)
                if run is None:
                    raise AgentRunNotFound("Agent run not found")
                return {
                    "principal_id": run.get("principal_id"),
                    "principal_source": run.get("principal_source"),
                    "principal_auth_epoch": run.get("principal_auth_epoch"),
                }

    def get_plan_version_for_owner(
        self,
        run_id: str,
        plan_version: int,
        *,
        student_id: str,
    ) -> Dict[str, Any]:
        """Read an immutable plan version and its owner-scoped step history."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if type(plan_version) is not int or plan_version < 1:
            raise ValueError("plan_version must be a positive integer")
        with self._read_connection() as conn:
            with conn.begin():
                run = self._select_run(conn, run_id=run_id, student_id=student_id)
                if run is None or plan_version > int(run["current_plan_version"] or 0):
                    raise AgentRunNotFound("Agent plan version not found")
                return self._plan_view(conn, run_id=run_id, plan_version=plan_version)

    def task_usage_for_owner(self, run_id: str, *, student_id: str) -> Dict[str, Any]:
        """Aggregate hash-verified planner and step usage for this owner's task."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self._read_connection() as conn:
            with conn.begin():
                run = self._select_run(conn, run_id=run_id, student_id=student_id)
                if run is None:
                    raise AgentRunNotFound("Agent run not found")
                rows = conn.execute(text("""
                    SELECT status, result_json, result_sha256
                    FROM agent_run_step WHERE run_id=:run_id
                    ORDER BY plan_version, step_index
                """), {"run_id": run_id}).mappings().all()
                totals = {"model_calls": 0, "tool_calls": 0, "input_tokens": 0,
                          "output_tokens": 0, "total_tokens": 0}

                def add_model_usage(model_usage: Any) -> Optional[str]:
                    if not isinstance(model_usage, Mapping):
                        return "usage_unverified"
                    usage_status = model_usage.get("status")
                    usage_reason = str(model_usage.get("reason") or "usage_unverified")
                    # A provider can report a call that exceeded its reserved
                    # per-phase token capacity. Keep those reported totals in
                    # the partial task accounting, but keep the aggregate
                    # unverified so no later phase can spend against it.
                    count_reported_overrun = (
                        usage_status != "observed"
                        and usage_reason == "turn_token_cap_exceeded"
                    )
                    if usage_status != "observed" and not count_reported_overrun:
                        return usage_reason
                    calls = model_usage.get("calls")
                    if not isinstance(calls, list) or len(calls) != model_usage.get("call_count"):
                        return "usage_call_count_mismatch"
                    for call in calls:
                        usage = call.get("usage") if isinstance(call, Mapping) else None
                        if not isinstance(usage, Mapping) or any(
                            type(usage.get(key)) is not int or usage[key] < 0
                            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                        ):
                            return "provider_usage_missing"
                        totals["input_tokens"] += usage["prompt_tokens"]
                        totals["output_tokens"] += usage["completion_tokens"]
                        totals["total_tokens"] += usage["total_tokens"]
                    totals["model_calls"] += len(calls)
                    return usage_reason if count_reported_overrun else None

                # A planner call belongs to a plan version, not to its first
                # execution step. Count every immutable version, including a
                # plan later superseded by a user revision.
                plan_rows = conn.execute(text("""
                    SELECT plan_version, plan_json, plan_sha256
                    FROM agent_run_plan WHERE run_id=:run_id
                    ORDER BY plan_version
                """), {"run_id": run_id}).mappings().all()
                for plan_row in plan_rows:
                    plan_json = _decode_json(
                        plan_row["plan_json"], expected_type=dict, field="plan",
                    )
                    _, plan_digest = _canonical_json(plan_json, max_bytes=16 * 1024)
                    if plan_digest != plan_row["plan_sha256"]:
                        raise AgentRunStoreError("Persisted Agent plan hash does not match")
                    metadata = plan_json.get("metadata") or {}
                    model_usage = metadata.get("task_model_usage")
                    if model_usage is None:
                        binding = metadata.get("capability_binding")
                        deterministic_capability = (
                            isinstance(binding, Mapping)
                            and binding.get("execution_pipeline") in {
                                "career_exploration_companion_v1", "student_task_v1",
                            }
                        )
                        if not deterministic_capability and metadata.get("executor_contract") == 1:
                            # A migrated active run may contain a pre-contract
                            # Lead plan whose planner usage was never captured.
                            return {"verified": False,
                                    "reason": "legacy_planner_usage_unverified", **totals}
                    else:
                        reason = add_model_usage(model_usage)
                        if reason:
                            return {"verified": False, "reason": reason, **totals}

                if self._task_contract_schema_available(conn):
                    followups = conn.execute(text("""
                        SELECT operation_status, model_required, reserved_model_calls,
                               model_usage_json
                        FROM agent_run_task_followup
                        WHERE run_id=:run_id AND student_id=:student_id
                        ORDER BY created_at, idempotency_key_sha256
                    """), {"run_id": run_id, "student_id": student_id}).mappings().all()
                    for operation in followups:
                        if not bool(operation["model_required"]):
                            continue
                        state = str(operation["operation_status"])
                        if state == "waiting_safe_point":
                            continue
                        if state in {"classifying", "needs_reconciliation"}:
                            totals["model_calls"] += int(
                                operation["reserved_model_calls"] or 0
                            )
                            return {
                                "verified": False,
                                "reason": "followup_classification_uncertain",
                                **totals,
                            }
                        if operation["model_usage_json"] is None:
                            return {"verified": False,
                                    "reason": "followup_usage_projection_missing", **totals}
                        followup_usage = _decode_json(
                            operation["model_usage_json"], expected_type=dict,
                            field="task follow-up model usage",
                        )
                        reason = add_model_usage(followup_usage)
                        if reason:
                            return {"verified": False, "reason": reason, **totals}

                for row in rows:
                    if row["status"] in {"running", "needs_reconciliation"}:
                        return {"verified": False, "reason": "active_or_uncertain_step",
                                **totals}
                    if row["result_json"] is None:
                        if row["status"] in {"completed", "failed", "superseded"}:
                            continue
                        continue
                    result = _decode_json(row["result_json"], expected_type=dict,
                                          field="step result")
                    _, digest = _canonical_json(result, max_bytes=_MAX_RESULT_BYTES)
                    if digest != row["result_sha256"]:
                        raise AgentRunStoreError("Persisted Agent step result hash does not match")
                    tool_count = result.get("tool_call_count")
                    if type(tool_count) is int and tool_count >= 0:
                        totals["tool_calls"] += tool_count
                    else:
                        tool_calls = result.get("tool_calls")
                        if isinstance(tool_calls, list):
                            totals["tool_calls"] += len(tool_calls)
                    model_usage = result.get("model_usage")
                    if model_usage is None:
                        if row["status"] in {"completed", "failed", "superseded"}:
                            return {"verified": False,
                                    "reason": "step_usage_projection_missing", **totals}
                        continue
                    reason = add_model_usage(model_usage)
                    if reason:
                        return {"verified": False, "reason": reason, **totals}
                if run.get("final_result_json") is not None:
                    final_result = _decode_json(
                        run["final_result_json"], expected_type=dict, field="final result",
                    )
                    _, final_digest = _canonical_json(
                        final_result, max_bytes=_MAX_RESULT_BYTES,
                    )
                    if final_digest != run.get("final_result_sha256"):
                        raise AgentRunStoreError("Persisted Agent final result hash does not match")
                    for field in ("planner_model_usage", "finalizer_model_usage"):
                        final_usage = final_result.get(field)
                        if final_usage is not None:
                            reason = add_model_usage(final_usage)
                            if reason:
                                return {"verified": False, "reason": reason, **totals}
                return {"verified": True, "reason": "usage_observed", **totals}

    def read_events(
        self,
        run_id: str,
        *,
        student_id: str,
        after_seq: int = 0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Owner-bound, per-run gap-free event page strictly after ``after_seq``."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        if type(after_seq) is not int or after_seq < 0:
            raise ValueError("after_seq must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._read_connection() as conn:
            with conn.begin():
                run = self._select_run(conn, run_id=run_id, student_id=student_id)
                if run is None:
                    raise AgentRunNotFound("Agent run not found")
                latest = int(run["last_event_seq"] or 0)
                if after_seq > latest:
                    raise ValueError("after_seq is ahead of the run event sequence")
                rows = list(conn.execute(text("""
                    SELECT event_seq, event_type, run_status, payload_json, occurred_at
                    FROM agent_run_event
                    WHERE run_id=:run_id AND student_id=:student_id AND event_seq>:after_seq
                    ORDER BY event_seq LIMIT :limit
                """), {
                    "run_id": run_id,
                    "student_id": student_id,
                    "after_seq": after_seq,
                    "limit": limit + 1,
                }).mappings())
                page = rows[:limit]
                events = [{
                    "event_seq": int(row["event_seq"]),
                    "event_type": row["event_type"],
                    "run_status": row["run_status"],
                    "payload": _decode_json(row["payload_json"], expected_type=dict, field="event payload"),
                    "occurred_at": _iso(row["occurred_at"]),
                } for row in page]
                return {
                    "run": self._run_view(run),
                    "events": events,
                    "cursor": events[-1]["event_seq"] if events else after_seq,
                    "latest_seq": latest,
                    "has_more": len(rows) > limit,
                }

    def _find_idempotent_run(self, *, student_id: str, session_id: str, key_sha256: str):
        with self._read_connection() as conn:
            with conn.begin():
                row = conn.execute(text("""
                    SELECT run_id, request_sha256 FROM agent_run
                    WHERE student_id=:student_id AND session_id=:session_id
                      AND idempotency_key_sha256=:key_hash
                    LIMIT 1
                """), {
                    "student_id": student_id,
                    "session_id": session_id,
                    "key_hash": key_sha256,
                }).mappings().first()
                return dict(row) if row else None

    def _select_run(self, conn, *, run_id: str, student_id: str):
        contract_columns = (
            "current_contract_revision, pause_requested_at,"
            if self._task_contract_schema_available(conn)
            else "0 AS current_contract_revision, NULL AS pause_requested_at,"
        )
        return conn.execute(text(f"""
            SELECT run_id, student_id, session_id, request_sha256,
                   input_snapshot_version, input_snapshot_redaction_version,
                   input_snapshot_bytes, input_snapshot_json, input_snapshot_sha256,
                   principal_id, principal_source, principal_auth_epoch,
                   status, current_plan_version, last_event_seq, cancel_requested_at,
                   {contract_columns}
                   final_result_json, final_result_sha256, finalized_at,
                   created_at, updated_at
            FROM agent_run WHERE run_id=:run_id AND student_id=:student_id
        """), {"run_id": run_id, "student_id": student_id}).mappings().first()

    def _locked_run(self, conn, *, run_id: str, student_id: str):
        suffix = " FOR UPDATE" if conn.dialect.name in {"mysql", "mariadb", "postgresql"} else ""
        contract_columns = (
            "current_contract_revision, pause_requested_at,"
            if self._task_contract_schema_available(conn)
            else "0 AS current_contract_revision, NULL AS pause_requested_at,"
        )
        row = conn.execute(text(f"""
            SELECT run_id, student_id, session_id, request_sha256,
                   input_snapshot_version, input_snapshot_redaction_version,
                   input_snapshot_bytes, input_snapshot_json, input_snapshot_sha256,
                   principal_id, principal_source, principal_auth_epoch,
                   status, current_plan_version, last_event_seq, cancel_requested_at,
                   {contract_columns}
                   final_result_json, final_result_sha256, finalized_at,
                   created_at, updated_at
            FROM agent_run WHERE run_id=:run_id AND student_id=:student_id
        """ + suffix), {"run_id": run_id, "student_id": student_id}).mappings().first()
        if row is None:
            raise AgentRunNotFound("Agent run not found")
        return row

    @staticmethod
    def _locked_dispatch_row(conn, run_id: str):
        suffix = " FOR UPDATE" if conn.dialect.name in {"mysql", "mariadb", "postgresql"} else ""
        row = conn.execute(text("""
            SELECT run_id, dispatch_state, publish_token, publish_lease_until,
                   claim_token, lease_until
            FROM agent_run_dispatch WHERE run_id=:run_id
        """ + suffix), {"run_id": run_id}).mappings().first()
        if row is None:
            raise AgentRunStoreError("Agent run dispatch row is missing")
        return row

    def _assert_current_claim(self, conn, run_id: str, claim_token: Optional[str]) -> None:
        """Fence worker mutations in the same transaction as the state change."""
        if claim_token is None:
            return
        if not isinstance(claim_token, str) or len(claim_token) != 32:
            raise AgentRunConflict("worker claim token is invalid")
        suffix = " FOR UPDATE" if conn.dialect.name in {"mysql", "mariadb", "postgresql"} else ""
        row = conn.execute(text("""
            SELECT dispatch_state, claim_token, lease_until
            FROM agent_run_dispatch WHERE run_id=:run_id
        """ + suffix), {"run_id": run_id}).mappings().first()
        if row is None or row["dispatch_state"] != "claimed" or row["claim_token"] != claim_token:
            raise AgentRunConflict("worker claim is stale or no longer owned")
        lease_until = row["lease_until"]
        if isinstance(lease_until, str):
            try:
                lease_until = datetime.fromisoformat(lease_until.replace("Z", "+00:00"))
                if lease_until.tzinfo is not None:
                    lease_until = lease_until.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                lease_until = None
        now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
            if conn.dialect.name in {"mysql", "mariadb"} else _utcnow()
        if lease_until is None or lease_until < now:
            raise AgentRunConflict("worker claim lease expired")

    def _locked_step(self, conn, run_id: str, plan_version: int, step_index: int):
        suffix = " FOR UPDATE" if conn.dialect.name in {"mysql", "mariadb", "postgresql"} else ""
        row = conn.execute(text("""
            SELECT run_id, plan_version, step_index, role, goal, skill_ids_json,
                   status, attempts, result_sha256, error_code
            FROM agent_run_step WHERE run_id=:run_id AND plan_version=:version
              AND step_index=:step_index
        """ + suffix), {
            "run_id": run_id,
            "version": plan_version,
            "step_index": step_index,
        }).mappings().first()
        if row is None:
            raise AgentRunNotFound("Agent plan step not found")
        return row

    def _append_event(
        self,
        conn,
        run,
        *,
        event_type: str,
        payload: Mapping[str, Any],
        new_status: Optional[str] = None,
        new_current_plan_version: Optional[int] = None,
        new_current_contract_revision: Optional[int] = None,
        new_cancel_requested_at: Any = ...,
        new_pause_requested_at: Any = ...,
    ) -> int:
        if event_type not in EVENT_TYPES:
            raise ValueError("event_type is not allowlisted")
        status = new_status if new_status is not None else str(run["status"])
        if status not in RUN_STATUSES:
            raise ValueError("run status is invalid")
        if not isinstance(payload, Mapping):
            raise ValueError("event payload must be a mapping")
        encoded_payload, _ = _canonical_json(dict(payload), max_bytes=8192)
        seq = int(run["last_event_seq"] or 0) + 1
        now = _utcnow()
        cancel_requested_at = (
            run.get("cancel_requested_at")
            if new_cancel_requested_at is ... else new_cancel_requested_at
        )
        current_plan_version = (
            int(run["current_plan_version"] or 0)
            if new_current_plan_version is None else new_current_plan_version
        )
        current_contract_revision = (
            int(run.get("current_contract_revision") or 0)
            if new_current_contract_revision is None else new_current_contract_revision
        )
        pause_requested_at = (
            run.get("pause_requested_at")
            if new_pause_requested_at is ... else new_pause_requested_at
        )
        contract_columns = self._task_contract_schema_available(conn)
        contract_update = (
            "current_contract_revision=:contract_revision, pause_requested_at=:pause_requested_at,"
            if contract_columns else ""
        )
        updated = conn.execute(text(f"""
            UPDATE agent_run SET status=:status, current_plan_version=:plan_version,
                {contract_update}
                last_event_seq=:event_seq, cancel_requested_at=:cancel_requested_at,
                updated_at=:updated_at
            WHERE run_id=:run_id AND student_id=:student_id AND last_event_seq=:previous_seq
        """), {
            "status": status,
            "plan_version": current_plan_version,
            "event_seq": seq,
            "cancel_requested_at": cancel_requested_at,
            "updated_at": now,
            "run_id": run["run_id"],
            "student_id": run["student_id"],
            "previous_seq": int(run["last_event_seq"] or 0),
            **({"contract_revision": current_contract_revision,
                "pause_requested_at": pause_requested_at} if contract_columns else {}),
        })
        if updated.rowcount != 1:
            raise AgentRunConflict("run event sequence changed concurrently")
        conn.execute(text("""
            INSERT INTO agent_run_event
            (run_id, student_id, event_seq, event_type, run_status, payload_json, occurred_at)
            VALUES (:run_id, :student_id, :event_seq, :event_type, :status, :payload, :now)
        """), {
            "run_id": run["run_id"],
            "student_id": run["student_id"],
            "event_seq": seq,
            "event_type": event_type,
            "status": status,
            "payload": encoded_payload,
            "now": now,
        })
        return seq

    def _plan_view(self, conn, *, run_id: str, plan_version: int) -> Dict[str, Any]:
        row = conn.execute(text("""
            SELECT plan_version, goal_summary, plan_json, plan_sha256, created_at
            FROM agent_run_plan WHERE run_id=:run_id AND plan_version=:version
        """), {"run_id": run_id, "version": plan_version}).mappings().first()
        if row is None:
            raise AgentRunStoreError("Current Agent plan version is missing")
        plan_json = _decode_json(row["plan_json"], expected_type=dict, field="plan")
        _, digest = _canonical_json(plan_json, max_bytes=16 * 1024)
        if digest != row["plan_sha256"]:
            raise AgentRunStoreError("Persisted Agent plan hash does not match")
        step_rows = list(conn.execute(text("""
            SELECT step_index, role, goal, skill_ids_json, status, attempts,
                   result_json, result_sha256, error_code, started_at, finished_at, updated_at
            FROM agent_run_step WHERE run_id=:run_id AND plan_version=:version
            ORDER BY step_index
        """), {"run_id": run_id, "version": plan_version}).mappings())
        steps = []
        for item in step_rows:
            result = None
            if item["result_json"] is not None:
                result = _decode_json(item["result_json"], expected_type=dict, field="step result")
                _, result_hash = _canonical_json(result, max_bytes=_MAX_RESULT_BYTES)
                if result_hash != item["result_sha256"]:
                    raise AgentRunStoreError("Persisted Agent step result hash does not match")
            steps.append({
                "step_index": int(item["step_index"]),
                "role": item["role"],
                "goal": item["goal"],
                "skill_ids": _decode_json(item["skill_ids_json"], expected_type=list, field="skill ids"),
                "status": item["status"],
                "attempts": int(item["attempts"] or 0),
                "result": result,
                "error_code": item["error_code"],
                "started_at": _iso(item["started_at"]),
                "finished_at": _iso(item["finished_at"]),
                "updated_at": _iso(item["updated_at"]),
            })
        return {
            "plan_version": int(row["plan_version"]),
            "goal_summary": row["goal_summary"],
            "plan_sha256": row["plan_sha256"],
            "steps": steps,
            "metadata": plan_json.get("metadata") or {},
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _verified_input_snapshot(row) -> Dict[str, Any]:
        snapshot = _decode_json(
            row["input_snapshot_json"], expected_type=dict, field="input snapshot",
        )
        encoded_snapshot, snapshot_sha256 = _canonical_json(
            snapshot, max_bytes=_MAX_INPUT_SNAPSHOT_BYTES,
        )
        snapshot_size = len(encoded_snapshot.encode("utf-8"))
        if (
            int(row["input_snapshot_version"]) != _INPUT_SNAPSHOT_VERSION
            or int(row["input_snapshot_redaction_version"]) != _INPUT_SNAPSHOT_REDACTION_VERSION
            or snapshot_size != int(row["input_snapshot_bytes"])
            or snapshot_sha256 != row["input_snapshot_sha256"]
            or snapshot_sha256 != row["request_sha256"]
            or snapshot.get("schema_version") != _INPUT_SNAPSHOT_VERSION
            or snapshot.get("redaction_version") != _INPUT_SNAPSHOT_REDACTION_VERSION
        ):
            raise AgentRunStoreError("Persisted Agent input snapshot metadata does not match")
        return snapshot

    @staticmethod
    def _run_view(row) -> Dict[str, Any]:
        AgentRunRepository._verified_input_snapshot(row)
        final_result = None
        if row["final_result_json"] is not None:
            final_result = _decode_json(
                row["final_result_json"], expected_type=dict, field="final result",
            )
            _, final_hash = _canonical_json(final_result, max_bytes=_MAX_RESULT_BYTES)
            if final_hash != row["final_result_sha256"]:
                raise AgentRunStoreError("Persisted Agent final result hash does not match")
        elif row["final_result_sha256"] is not None or row["finalized_at"] is not None:
            raise AgentRunStoreError("Persisted Agent final result metadata is incomplete")
        return {
            "run_id": row["run_id"],
            "student_id": row["student_id"],
            "session_id": row["session_id"],
            "request_sha256": row["request_sha256"],
            "input_snapshot_version": int(row["input_snapshot_version"]),
            "input_snapshot_redaction_version": int(row["input_snapshot_redaction_version"]),
            "input_snapshot_bytes": int(row["input_snapshot_bytes"]),
            "input_snapshot_sha256": row["input_snapshot_sha256"],
            "status": row["status"],
            "current_plan_version": int(row["current_plan_version"] or 0),
            "current_contract_revision": int(row.get("current_contract_revision") or 0),
            "last_event_seq": int(row["last_event_seq"] or 0),
            "cancel_requested": row.get("cancel_requested_at") is not None,
            "cancel_requested_at": _iso(row.get("cancel_requested_at")),
            "pause_requested": row.get("pause_requested_at") is not None,
            "final_result": final_result,
            "final_result_checkpoint": bool(
                final_result is not None and row["finalized_at"] is None
                and row["status"] in {"paused", "finalizing"}
            ),
            "final_result_sha256": row["final_result_sha256"],
            "finalized_at": _iso(row["finalized_at"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    @staticmethod
    def _validate_version_index(plan_version: int, step_index: int) -> None:
        if type(plan_version) is not int or plan_version < 1:
            raise ValueError("plan_version must be a positive integer")
        if type(step_index) is not int or step_index < 1:
            raise ValueError("step_index must be a positive integer")

    def _read_connection(self):
        from contextlib import contextmanager

        @contextmanager
        def connection():
            conn = self.mysql.engine.connect()
            if conn.dialect.name in {"mysql", "mariadb"}:
                conn = conn.execution_options(isolation_level="REPEATABLE READ")
            try:
                yield conn
            finally:
                conn.close()

        return connection()

    def _utc_transaction(self):
        from ..db.mysql_client import utc_transaction
        return utc_transaction(self.mysql)


_repository: Optional[AgentRunRepository] = None


def get_agent_run_repository() -> AgentRunRepository:
    global _repository
    if _repository is None:
        _repository = AgentRunRepository()
    return _repository
