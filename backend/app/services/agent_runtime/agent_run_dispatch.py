"""MySQL outbox and fenced claim state for Agent Celery delivery.

MySQL owns intent, publish retries, worker claims, cancellation and uncertainty.
Celery transports only ``run_id``; neither Redis nor Celery task state is a
business result. Expired claims may be replayed only at a DB-proven safe
boundary; a running/uncertain step is never automatically repeated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import text

from .agent_run_store import (
    AgentRunConflict,
    AgentRunNotFound,
    AgentRunRepository,
    TERMINAL_RUN_STATUSES,
    _run_id_value,
    _owner_value,
)


DISPATCH_STATES = frozenset({
    "pending", "publishing", "published", "claimed", "waiting_executor",
    "not_dispatched", "cancelled", "terminal", "needs_reconciliation",
    "paused", "waiting_input", "waiting_authorization",
})
DEFAULT_PUBLISH_LEASE_SECONDS = 30
DEFAULT_REPUBLISH_AFTER_SECONDS = 60
DEFAULT_CLAIM_LEASE_SECONDS = 900
MAX_REPLAY_PAGE_SIZE = 200


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    return None


class AgentRunDispatchStore:
    """Transactionally mutate dispatch state together with the run event log."""

    def __init__(self, repository: Optional[AgentRunRepository] = None):
        self.repository = repository or AgentRunRepository()

    @property
    def mysql(self):
        return self.repository.mysql

    @staticmethod
    def _lock_suffix(conn) -> str:
        return " FOR UPDATE" if conn.dialect.name in {"mysql", "mariadb", "postgresql"} else ""

    def _locked_run(self, conn, run_id: str):
        contract_columns = (
            "current_contract_revision, pause_requested_at,"
            if self.repository._task_contract_schema_available(conn)
            else "0 AS current_contract_revision, NULL AS pause_requested_at,"
        )
        row = conn.execute(text(f"""
            SELECT run_id, student_id, session_id, request_sha256,
                   input_snapshot_version, input_snapshot_redaction_version,
                   input_snapshot_bytes, input_snapshot_json, input_snapshot_sha256,
                   status, current_plan_version, last_event_seq, cancel_requested_at,
                   {contract_columns}
                   final_result_json, final_result_sha256, finalized_at,
                   created_at, updated_at
            FROM agent_run WHERE run_id=:run_id
        """ + self._lock_suffix(conn)), {"run_id": run_id}).mappings().first()
        if row is None:
            raise AgentRunNotFound("Agent run not found")
        return row

    def _locked_dispatch(self, conn, run_id: str):
        row = conn.execute(text("""
            SELECT run_id, dispatch_state, publish_token, publish_lease_until,
                   publish_attempts, next_publish_at, last_enqueued_at,
                   claim_token, lease_until, claim_attempts, last_error_code,
                   created_at, updated_at
            FROM agent_run_dispatch WHERE run_id=:run_id
        """ + self._lock_suffix(conn)), {"run_id": run_id}).mappings().first()
        if row is None:
            raise RuntimeError("Agent run dispatch intent is missing")
        if row["dispatch_state"] not in DISPATCH_STATES:
            raise RuntimeError("Agent run dispatch state is invalid")
        return row

    def _append(self, conn, run, event_type: str, payload: Dict[str, Any], *, new_status=None,
                new_cancel_requested_at=...):
        return self.repository._append_event(
            conn,
            run,
            event_type=event_type,
            payload=payload,
            new_status=new_status,
            new_cancel_requested_at=new_cancel_requested_at,
        )

    @staticmethod
    def _active_or_uncertain_steps(conn, run) -> int:
        version = int(run["current_plan_version"] or 0)
        if version < 1:
            return 0
        return int(conn.execute(text("""
            SELECT COUNT(*) FROM agent_run_step
            WHERE run_id=:run_id AND plan_version=:version
              AND status IN ('running','needs_reconciliation')
        """), {"run_id": run["run_id"], "version": version}).scalar_one() or 0)

    def _safe_to_resume(self, conn, run) -> bool:
        """Resume only durable phases whose last model/write result is known."""
        status = str(run["status"])
        if status == "planning":
            # A Lead call may have completed just before the worker vanished;
            # there is no persisted output/usage checkpoint to justify replay.
            return False
        if status == "finalizing" and not (
            run.get("final_result_json") is not None
            and run.get("finalized_at") is None
        ):
            # Only the pause-safe finalizer draft is replayable. An unknown
            # finalizer result must be reconciled instead of billed twice.
            return False
        return (
            status in {
                "queued", "planned", "running",
                "steps_completed", "finalizing",
            }
            and run.get("cancel_requested_at") is None
            and self._active_or_uncertain_steps(conn, run) == 0
        )

    def _requeue_expired_claim(self, conn, run, dispatch, *, now: datetime) -> bool:
        if not self._safe_to_resume(conn, run):
            self._needs_reconciliation(
                conn, run, dispatch, reason="worker_claim_expired", now=now,
            )
            return False
        conn.execute(text("""
            UPDATE agent_run_dispatch SET dispatch_state='pending',
                claim_token=NULL, lease_until=NULL, last_error_code=NULL, updated_at=:now
            WHERE run_id=:run_id
        """), {"run_id": run["run_id"], "now": now})
        self._append(conn, run, "run.dispatch_requeued", {
            "reason": "claim_expired_safe_boundary",
        })
        return True

    def _cancel_if_safe(self, conn, run, dispatch, *, now: datetime) -> bool:
        if run["status"] in TERMINAL_RUN_STATUSES or run["status"] == "needs_reconciliation":
            return run["status"] == "cancelled"
        if run.get("cancel_requested_at") is None:
            return False
        if self._active_or_uncertain_steps(conn, run):
            return False
        version = int(run["current_plan_version"] or 0)
        if version:
            conn.execute(text("""
                UPDATE agent_run_step SET status='cancelled', finished_at=:now
                WHERE run_id=:run_id AND plan_version=:version AND status='pending'
            """), {"run_id": run["run_id"], "version": version, "now": now})
        self._append(
            conn, run, "run.cancelled", {"safe_point": True},
            new_status="cancelled",
        )
        conn.execute(text("""
            UPDATE agent_run_dispatch SET dispatch_state='cancelled',
                publish_token=NULL, publish_lease_until=NULL,
                claim_token=NULL, lease_until=NULL, updated_at=:now
            WHERE run_id=:run_id
        """), {"run_id": run["run_id"], "now": now})
        return True

    def _needs_reconciliation(self, conn, run, dispatch, *, reason: str, now: datetime) -> None:
        version = int(run["current_plan_version"] or 0)
        if version:
            conn.execute(text("""
                UPDATE agent_run_step SET status='needs_reconciliation',
                    error_code='worker_claim_expired', finished_at=:now
                WHERE run_id=:run_id AND plan_version=:version AND status='running'
            """), {"run_id": run["run_id"], "version": version, "now": now})
        if run["status"] != "needs_reconciliation":
            self._append(
                conn, run, "run.dispatch_needs_reconciliation",
                {"reason": reason}, new_status="needs_reconciliation",
            )
        conn.execute(text("""
            UPDATE agent_run_dispatch SET dispatch_state='needs_reconciliation',
                publish_token=NULL, publish_lease_until=NULL,
                claim_token=NULL, lease_until=NULL, last_error_code=:reason,
                updated_at=:now
            WHERE run_id=:run_id
        """), {"run_id": run["run_id"], "reason": reason, "now": now})

    def get_for_owner(self, run_id: str, *, student_id: str) -> Dict[str, Any]:
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        row = self.mysql.fetch_one("""
            SELECT d.dispatch_state, d.publish_attempts, d.claim_attempts,
                   d.last_enqueued_at, d.updated_at
            FROM agent_run_dispatch d JOIN agent_run r ON r.run_id=d.run_id
            WHERE d.run_id=:run_id AND r.student_id=:student_id
        """, {"run_id": run_id, "student_id": student_id})
        if row is None:
            raise AgentRunNotFound("Agent run not found")
        return {
            "status": row["dispatch_state"],
            "publish_attempts": int(row["publish_attempts"] or 0),
            "claim_attempts": int(row["claim_attempts"] or 0),
            "last_enqueued_at": row["last_enqueued_at"].isoformat()
            if hasattr(row["last_enqueued_at"], "isoformat") else row["last_enqueued_at"],
            "updated_at": row["updated_at"].isoformat()
            if hasattr(row["updated_at"], "isoformat") else row["updated_at"],
        }

    def reserve_publish(
        self,
        run_id: str,
        *,
        publish_lease_seconds: int = DEFAULT_PUBLISH_LEASE_SECONDS,
        republish_after_seconds: int = DEFAULT_REPUBLISH_AFTER_SECONDS,
        allow_not_dispatched: bool = False,
    ) -> Dict[str, Any]:
        """Reserve a MySQL outbox row before publishing; expiry makes it replayable."""
        run_id = _run_id_value(run_id)
        if type(publish_lease_seconds) is not int or not 1 <= publish_lease_seconds <= 300:
            raise ValueError("publish_lease_seconds must be 1..300")
        if type(republish_after_seconds) is not int or not 5 <= republish_after_seconds <= 3600:
            raise ValueError("republish_after_seconds must be 5..3600")
        if type(allow_not_dispatched) is not bool:
            raise ValueError("allow_not_dispatched must be bool")
        token = uuid.uuid4().hex
        with self.repository._utc_transaction() as conn:
            run = self._locked_run(conn, run_id)
            dispatch = self._locked_dispatch(conn, run_id)
            now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else _now()

            if run["status"] in TERMINAL_RUN_STATUSES:
                terminal_state = "cancelled" if run["status"] == "cancelled" else "terminal"
                conn.execute(text("UPDATE agent_run_dispatch SET dispatch_state=:state,"
                                  "updated_at=:now WHERE run_id=:run_id"),
                             {"state": terminal_state,
                              "run_id": run_id, "now": now})
                return {"state": terminal_state, "reserved": False}
            if run["status"] == "needs_reconciliation":
                self._needs_reconciliation(conn, run, dispatch,
                                           reason="run_needs_reconciliation", now=now)
                return {"state": "needs_reconciliation", "reserved": False}
            if run["status"] in {"paused", "waiting_input", "waiting_authorization"}:
                state = str(run["status"])
                if dispatch["dispatch_state"] != state:
                    conn.execute(text("""
                        UPDATE agent_run_dispatch SET dispatch_state=:state,
                            publish_token=NULL, publish_lease_until=NULL,
                            claim_token=NULL, lease_until=NULL, updated_at=:now
                        WHERE run_id=:run_id
                    """), {"state": state, "run_id": run_id, "now": now})
                return {"state": state, "reserved": False}
            if self._cancel_if_safe(conn, run, dispatch, now=now):
                return {"state": "cancelled", "reserved": False}

            state = dispatch["dispatch_state"]
            if state == "not_dispatched":
                # 0009 deliberately holds rows submitted before the outbox was
                # introduced. Only an explicit idempotent HTTP resubmission
                # may promote one; the periodic replay scanner never does.
                if not allow_not_dispatched:
                    return {"state": state, "reserved": False}
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='pending',
                        last_error_code=NULL, next_publish_at=:now, updated_at=:now
                    WHERE run_id=:run_id AND dispatch_state='not_dispatched'
                """), {"run_id": run_id, "now": now})
                self._append(conn, run, "run.dispatch_requeued", {
                    "reason": "explicit_idempotent_resubmission",
                })
                state = "pending"
            if state == "waiting_executor" and allow_not_dispatched:
                # The old transport-only executor has no plan or business
                # progress. An explicit idempotent HTTP resubmission may hand
                # it to the newly deployed runner; Beat never releases it.
                if (run["status"] != "queued"
                        or int(run["current_plan_version"] or 0) != 0
                        or self._active_or_uncertain_steps(conn, run)):
                    return {"state": "waiting_executor", "reserved": False}
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='pending',
                        next_publish_at=:now, updated_at=:now
                    WHERE run_id=:run_id AND dispatch_state='waiting_executor'
                """), {"run_id": run_id, "now": now})
                self._append(conn, run, "run.dispatch_requeued", {
                    "reason": "explicit_resubmission_after_executor_deploy",
                })
                state = "pending"
            if state in {"waiting_executor", "cancelled", "terminal", "needs_reconciliation"}:
                return {"state": state, "reserved": False}
            if dispatch["claim_token"] is not None:
                claim_lease_until = _as_datetime(dispatch["lease_until"])
                if claim_lease_until is not None and claim_lease_until >= now:
                    return {"state": "claimed", "reserved": False}
                if not self._requeue_expired_claim(conn, run, dispatch, now=now):
                    return {"state": "needs_reconciliation", "reserved": False}
                state = "pending"

            publish_lease_until = _as_datetime(dispatch["publish_lease_until"])
            if (dispatch["publish_token"] is not None
                    and publish_lease_until is not None
                    and publish_lease_until >= now):
                return {"state": "publishing", "reserved": False}
            last_enqueued_at = _as_datetime(dispatch["last_enqueued_at"])
            if (state == "published" and last_enqueued_at is not None
                    and last_enqueued_at + timedelta(seconds=republish_after_seconds) > now):
                return {"state": "published", "reserved": False}
            next_publish_at = _as_datetime(dispatch["next_publish_at"])
            if next_publish_at is not None and next_publish_at > now:
                return {"state": "pending", "reserved": False}

            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='publishing',
                    publish_token=:token,
                    publish_lease_until=:lease_until,
                    publish_attempts=publish_attempts+1, updated_at=:now
                WHERE run_id=:run_id
            """), {
                "token": token,
                "lease_until": now + timedelta(seconds=publish_lease_seconds),
                "now": now,
                "run_id": run_id,
            })
        return {"state": "publishing", "reserved": True, "publish_token": token}

    def finish_publish(
        self,
        run_id: str,
        *,
        publish_token: str,
        published: bool,
        republish_after_seconds: int = DEFAULT_REPUBLISH_AFTER_SECONDS,
        error_code: str = "broker_publish_failed",
    ) -> str:
        """Persist broker acknowledgement or bounded retry time; never trust result backend."""
        run_id = _run_id_value(run_id)
        if type(published) is not bool:
            raise ValueError("published must be bool")
        if not isinstance(publish_token, str) or len(publish_token) != 32:
            raise ValueError("publish_token is invalid")
        with self.repository._utc_transaction() as conn:
            run = self._locked_run(conn, run_id)
            dispatch = self._locked_dispatch(conn, run_id)
            now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else _now()
            if dispatch["publish_token"] != publish_token or dispatch["dispatch_state"] != "publishing":
                return str(dispatch["dispatch_state"])
            if published:
                next_publish_at = now + timedelta(seconds=republish_after_seconds)
                conn.execute(text("""
                    UPDATE agent_run_dispatch SET dispatch_state='published',
                        publish_token=NULL, publish_lease_until=NULL,
                        last_enqueued_at=:now, next_publish_at=:next_publish_at,
                        last_error_code=NULL, updated_at=:now
                    WHERE run_id=:run_id AND publish_token=:token
                """), {"now": now, "next_publish_at": next_publish_at,
                      "run_id": run_id, "token": publish_token})
                self._append(conn, run, "run.dispatch_published", {"transport": "celery"})
                return "published"

            attempts = int(dispatch["publish_attempts"] or 1)
            backoff_seconds = min(15 * (2 ** min(max(attempts - 1, 0), 5)), 480)
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='pending',
                    publish_token=NULL, publish_lease_until=NULL,
                    next_publish_at=:next_publish_at, last_error_code=:error_code,
                    updated_at=:now
                WHERE run_id=:run_id AND publish_token=:token
            """), {"next_publish_at": now + timedelta(seconds=backoff_seconds),
                  "error_code": error_code, "now": now,
                  "run_id": run_id, "token": publish_token})
            self._append(conn, run, "run.dispatch_retry_scheduled", {
                "reason": error_code,
                "retry_after_seconds": backoff_seconds,
            })
            return "pending"

    def claim_worker(
        self,
        run_id: str,
        *,
        lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
    ) -> Optional[Dict[str, Any]]:
        """Fence duplicate Celery deliveries with a MySQL claim token and lease."""
        run_id = _run_id_value(run_id)
        if type(lease_seconds) is not int or not 30 <= lease_seconds <= 6 * 60 * 60:
            raise ValueError("lease_seconds must be 30..21600")
        token = uuid.uuid4().hex
        with self.repository._utc_transaction() as conn:
            run = self._locked_run(conn, run_id)
            dispatch = self._locked_dispatch(conn, run_id)
            now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else _now()

            if run["status"] in TERMINAL_RUN_STATUSES:
                terminal_state = "cancelled" if run["status"] == "cancelled" else "terminal"
                conn.execute(text("UPDATE agent_run_dispatch SET dispatch_state=:state,"
                                  "updated_at=:now WHERE run_id=:run_id"),
                             {"state": terminal_state, "run_id": run_id, "now": now})
                return None
            if run["status"] == "needs_reconciliation":
                conn.execute(text("UPDATE agent_run_dispatch SET dispatch_state='needs_reconciliation',"
                                  "updated_at=:now WHERE run_id=:run_id"),
                             {"run_id": run_id, "now": now})
                return None
            if self._cancel_if_safe(conn, run, dispatch, now=now):
                return None
            if dispatch["dispatch_state"] in {
                "waiting_executor", "cancelled", "terminal", "needs_reconciliation",
                "paused", "waiting_input", "waiting_authorization",
            }:
                return None
            if dispatch["claim_token"] is not None:
                claim_lease_until = _as_datetime(dispatch["lease_until"])
                if claim_lease_until is not None and claim_lease_until >= now:
                    return None
                if not self._requeue_expired_claim(conn, run, dispatch, now=now):
                    return None
                state = "pending"
            else:
                state = dispatch["dispatch_state"]

            if run["status"] not in {
                "queued", "planning", "planned", "running", "steps_completed", "finalizing",
            }:
                return None
            if state not in {"pending", "publishing", "published"}:
                return None
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='claimed',
                    claim_token=:token, lease_until=:lease_until,
                    publish_token=NULL, publish_lease_until=NULL,
                    claim_attempts=claim_attempts+1, updated_at=:now
                WHERE run_id=:run_id
            """), {"token": token, "lease_until": now + timedelta(seconds=lease_seconds),
                  "now": now, "run_id": run_id})
            self._append(conn, run, "run.dispatch_claimed", {
                "claim_attempt": int(dispatch["claim_attempts"] or 0) + 1,
            })
            return {
                "run_id": run_id,
                "student_id": run["student_id"],
                "session_id": run["session_id"],
                "claim_token": token,
                "lease_until": (now + timedelta(seconds=lease_seconds)).isoformat(),
            }

    def mark_waiting_executor(self, run_id: str, *, claim_token: str) -> str:
        """Acknowledge delivery without pretending the Agent executor ran."""
        run_id = _run_id_value(run_id)
        if not isinstance(claim_token, str) or len(claim_token) != 32:
            raise ValueError("claim_token is invalid")
        with self.repository._utc_transaction() as conn:
            run = self._locked_run(conn, run_id)
            dispatch = self._locked_dispatch(conn, run_id)
            now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else _now()
            if dispatch["claim_token"] != claim_token or dispatch["dispatch_state"] != "claimed":
                raise AgentRunConflict("worker claim is stale or no longer owned")
            lease_until = _as_datetime(dispatch["lease_until"])
            if lease_until is None or lease_until < now:
                raise AgentRunConflict("worker claim lease expired")
            if run["status"] != "queued":
                self._needs_reconciliation(
                    conn, run, dispatch, reason="executor_unavailable_after_progress", now=now,
                )
                return "needs_reconciliation"
            if self._cancel_if_safe(conn, run, dispatch, now=now):
                return "cancelled"
            conn.execute(text("""
                UPDATE agent_run_dispatch SET dispatch_state='waiting_executor',
                    claim_token=NULL, lease_until=NULL, updated_at=:now
                WHERE run_id=:run_id AND claim_token=:token
            """), {"run_id": run_id, "token": claim_token, "now": now})
            self._append(conn, run, "run.executor_unavailable", {
                "reason": "agent_executor_not_connected",
            })
            return "waiting_executor"

    def renew_claim(
        self,
        run_id: str,
        *,
        claim_token: str,
        lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
    ) -> bool:
        """Extend only the current worker's lease at a verified safe boundary."""
        run_id = _run_id_value(run_id)
        if type(lease_seconds) is not int or not 30 <= lease_seconds <= 6 * 60 * 60:
            raise ValueError("lease_seconds must be 30..21600")
        now = _now()
        with self.repository._utc_transaction() as conn:
            run = self._locked_run(conn, run_id)
            dispatch = self._locked_dispatch(conn, run_id)
            db_now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else now
            if (dispatch["dispatch_state"] != "claimed"
                    or dispatch["claim_token"] != claim_token
                    or _as_datetime(dispatch["lease_until"]) is None
                    or _as_datetime(dispatch["lease_until"]) < db_now
                    or run["status"] in TERMINAL_RUN_STATUSES | {"needs_reconciliation"}):
                return False
            conn.execute(text("""
                UPDATE agent_run_dispatch SET lease_until=:lease_until, updated_at=:now
                WHERE run_id=:run_id AND claim_token=:token
            """), {"lease_until": db_now + timedelta(seconds=lease_seconds),
                  "now": db_now, "run_id": run_id, "token": claim_token})
            return True

    def claim_is_current(self, run_id: str, claim_token: str) -> bool:
        """Re-read the MySQL claim before authorizing a worker-scoped tool call."""
        run_id = _run_id_value(run_id)
        if not isinstance(claim_token, str) or len(claim_token) != 32:
            return False
        with self.repository._utc_transaction() as conn:
            row = conn.execute(text("""
                SELECT dispatch_state, claim_token, lease_until
                FROM agent_run_dispatch WHERE run_id=:run_id
            """), {"run_id": run_id}).mappings().first()
            if row is None or row["dispatch_state"] != "claimed" or row["claim_token"] != claim_token:
                return False
            now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else _now()
            lease_until = _as_datetime(row["lease_until"])
            return lease_until is not None and lease_until >= now

    def get_claim_binding(self, run_id: str, claim_token: str) -> Optional[Dict[str, Any]]:
        """Load the worker Principal binding from the claimed MySQL run row."""
        run_id = _run_id_value(run_id)
        if not isinstance(claim_token, str) or len(claim_token) != 32:
            return None
        with self.repository._utc_transaction() as conn:
            now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else _now()
            row = conn.execute(text("""
                SELECT r.student_id, r.principal_id, r.principal_source,
                       r.principal_auth_epoch, d.dispatch_state, d.claim_token, d.lease_until
                FROM agent_run r JOIN agent_run_dispatch d ON d.run_id=r.run_id
                WHERE r.run_id=:run_id
            """), {"run_id": run_id}).mappings().first()
            if (row is None or row["dispatch_state"] != "claimed"
                    or row["claim_token"] != claim_token):
                return None
            lease_until = _as_datetime(row["lease_until"])
            if lease_until is None or lease_until < now:
                return None
            return {
                "student_id": str(row["student_id"]),
                "principal_id": row["principal_id"],
                "principal_source": row["principal_source"],
                "principal_auth_epoch": row["principal_auth_epoch"],
            }

    def cancel_if_safe(self, run_id: str, *, student_id: str) -> Dict[str, Any]:
        """Settle a pending cancel only if no running/uncertain step exists."""
        run_id = _run_id_value(run_id)
        student_id = _owner_value(student_id, field="student_id")
        with self.repository._utc_transaction() as conn:
            contract_columns = (
                "current_contract_revision, pause_requested_at,"
                if self.repository._task_contract_schema_available(conn)
                else "0 AS current_contract_revision, NULL AS pause_requested_at,"
            )
            run = conn.execute(text(f"""
                SELECT run_id, student_id, session_id, request_sha256,
                       input_snapshot_version, input_snapshot_redaction_version,
                       input_snapshot_bytes, input_snapshot_json, input_snapshot_sha256,
                       status, current_plan_version, last_event_seq, cancel_requested_at,
                       {contract_columns}
                       final_result_json, final_result_sha256, finalized_at, created_at, updated_at
                FROM agent_run WHERE run_id=:run_id AND student_id=:student_id
            """ + self._lock_suffix(conn)), {
                "run_id": run_id, "student_id": student_id,
            }).mappings().first()
            if run is None:
                raise AgentRunNotFound("Agent run not found")
            dispatch = self._locked_dispatch(conn, run_id)
            now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else _now()
            self._cancel_if_safe(conn, run, dispatch, now=now)
        return self.repository.get_for_owner(run_id, student_id=student_id)

    def due_run_ids(
        self,
        *,
        limit: int = 50,
        republish_after_seconds: int = DEFAULT_REPUBLISH_AFTER_SECONDS,
    ) -> list[str]:
        if type(limit) is not int or not 1 <= limit <= MAX_REPLAY_PAGE_SIZE:
            raise ValueError("limit must be 1..200")
        with self.repository._utc_transaction() as conn:
            now = conn.execute(text("SELECT UTC_TIMESTAMP(6)")).scalar_one() \
                if conn.dialect.name in {"mysql", "mariadb"} else _now()
            cutoff = now - timedelta(seconds=republish_after_seconds)
            rows = conn.execute(text("""
                SELECT d.run_id
                FROM agent_run_dispatch d JOIN agent_run r ON r.run_id=d.run_id
                WHERE r.status NOT IN ('completed','failed','cancelled','partial',
                    'needs_reconciliation','paused','waiting_input','waiting_authorization')
                  AND (
                    (d.dispatch_state='pending' AND d.next_publish_at<=:now)
                    OR (d.dispatch_state='publishing' AND d.publish_lease_until<=:now)
                    OR (d.dispatch_state='published' AND d.last_enqueued_at<=:cutoff)
                    OR (d.dispatch_state='claimed' AND d.lease_until<=:now)
                  )
                ORDER BY COALESCE(d.last_enqueued_at,d.next_publish_at), d.run_id
                LIMIT :limit
            """), {"now": now, "cutoff": cutoff, "limit": limit}).mappings().all()
        return [str(row["run_id"]) for row in rows]


_dispatch_store: Optional[AgentRunDispatchStore] = None


def get_agent_run_dispatch_store() -> AgentRunDispatchStore:
    global _dispatch_store
    if _dispatch_store is None:
        _dispatch_store = AgentRunDispatchStore()
    return _dispatch_store
