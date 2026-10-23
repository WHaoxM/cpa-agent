"""Durable admission and replay for explicitly keyed Agent HTTP turns.

This is deliberately a small request seam, not a general workflow engine.
MySQL owns the admission/tombstone because a Redis AOF acknowledgement can be
lost around a process or host crash.  Redis session leases still serialize the
mutable conversation state; the two mechanisms solve different problems.

The ledger never retries execution.  A stale or ambiguously settled turn is
``unknown`` and requires reconciliation with authoritative session/artifact
state before a caller chooses a new idempotency key.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import threading
import uuid
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ...config import Config
from ..db.mysql_client import utc_transaction
from ...utils.logger import get_logger
from ..db import get_mysql_client


logger = get_logger("cpa-agent.agent.turn-idempotency")


_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


TURN_IDEMPOTENCY_DDL = """
CREATE TABLE IF NOT EXISTS agent_turn_idempotency (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    session_id          VARCHAR(64)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    key_sha256          CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256      CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    state               VARCHAR(16)  NOT NULL COMMENT 'reserved/running/completed/unknown/expired',
    execution_token     CHAR(32)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    response_status     SMALLINT UNSIGNED DEFAULT NULL,
    response_json       MEDIUMTEXT   DEFAULT NULL,
    stale_after         DATETIME(6)  NOT NULL,
    response_expires_at DATETIME(6)  NOT NULL,
    created_at          DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at          DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                      ON UPDATE CURRENT_TIMESTAMP(6),
    completed_at        DATETIME(6)  DEFAULT NULL,
    UNIQUE KEY uk_agent_turn_idempotency (student_id, session_id, key_sha256),
    INDEX idx_agent_turn_idempotency_expiry (state, response_expires_at),
    INDEX idx_agent_turn_idempotency_session (student_id, session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Explicit Agent HTTP idempotency admission and public result replay'
"""


class TurnIdempotencyError(RuntimeError):
    """Base for stable public admission failures."""


class InvalidIdempotencyKey(ValueError):
    pass


class TurnIdempotencyConflict(TurnIdempotencyError):
    pass


class TurnIdempotencyInProgress(TurnIdempotencyError):
    pass


class TurnIdempotencyUnknown(TurnIdempotencyError):
    pass


class TurnIdempotencyExpired(TurnIdempotencyError):
    pass


class TurnIdempotencyStorageUnavailable(TurnIdempotencyError):
    pass


class TurnIdempotencySettlementLost(TurnIdempotencyError):
    pass


@dataclass(frozen=True)
class TurnClaim:
    student_id: str
    session_id: str
    key_sha256: str
    request_sha256: str
    execution_token: str


@dataclass(frozen=True)
class TurnAdmission:
    claim: Optional[TurnClaim] = None
    replay_response: Optional[dict[str, Any]] = None
    replay_status: int = 200

    @property
    def replayed(self) -> bool:
        return self.replay_response is not None


def validate_idempotency_key(value: str) -> str:
    value = str(value or "")
    if not _KEY.fullmatch(value):
        raise InvalidIdempotencyKey(
            "Idempotency-Key 必须为 8 至 128 位字母、数字或 _ . : -"
        )
    return value


def canonical_request_sha256(payload: Mapping[str, Any]) -> str:
    """Hash the normalized semantic input, never raw aliases or transport."""
    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("幂等请求包含不可规范化的字段") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def scoped_key_sha256(student_id: str, session_id: str, key: str) -> str:
    key = validate_idempotency_key(key)
    material = json.dumps(
        [str(student_id), str(session_id), key],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def install_turn_idempotency_schema(mysql=None) -> None:
    """Install the additive table explicitly (migration/startup/test hook)."""
    mysql = mysql or get_mysql_client()
    mysql.execute(TURN_IDEMPOTENCY_DDL, commit=True)


class TurnIdempotencyLedger:
    """MySQL unique-key/CAS adapter for one explicitly keyed HTTP turn."""

    def __init__(self, mysql=None, *, stale_seconds: Optional[int] = None,
                 retention_days: Optional[int] = None):
        self.mysql = mysql or get_mysql_client()
        self.stale_seconds = max(
            300,
            int(stale_seconds or Config.AGENT_TURN_IDEMPOTENCY_STALE_SECONDS),
        )
        self.retention_days = max(
            1,
            int(retention_days or Config.AGENT_TURN_IDEMPOTENCY_RETENTION_DAYS),
        )

    def admit(self, *, student_id: str, session_id: str, key: str,
              request_sha256: str) -> TurnAdmission:
        key_sha256 = scoped_key_sha256(student_id, session_id, key)
        token = uuid.uuid4().hex
        params = {
            "sid": student_id,
            "session": session_id,
            "key_hash": key_sha256,
            "request_hash": request_sha256,
            "token": token,
            "stale_seconds": self.stale_seconds,
            "retention_days": self.retention_days,
        }
        post_commit_error: Optional[TurnIdempotencyError] = None
        try:
            with utc_transaction(self.mysql) as conn:
                conn.execute(text(
                    "INSERT INTO agent_turn_idempotency "
                    "(student_id, session_id, key_sha256, request_sha256, state, "
                    "execution_token, stale_after, response_expires_at) "
                    "VALUES (:sid, :session, :key_hash, :request_hash, 'reserved', "
                    ":token, TIMESTAMPADD(SECOND, :stale_seconds, UTC_TIMESTAMP(6)), "
                    "TIMESTAMPADD(DAY, :retention_days, UTC_TIMESTAMP(6)))"
                ), params)
            return TurnAdmission(claim=TurnClaim(
                student_id=student_id,
                session_id=session_id,
                key_sha256=key_sha256,
                request_sha256=request_sha256,
                execution_token=token,
            ))
        except IntegrityError as exc:
            # Do not use INSERT IGNORE here: it also downgrades truncation and
            # NOT NULL errors to warnings.  Only the unique-key race is an
            # existing admission; every other storage error fails closed.
            code = getattr(getattr(exc, "orig", None), "args", [None])[0]
            if code != 1062:
                raise TurnIdempotencyStorageUnavailable("幂等存储不可用") from exc
        except Exception as exc:
            raise TurnIdempotencyStorageUnavailable("幂等存储不可用") from exc

        try:
            with utc_transaction(self.mysql) as conn:
                row = conn.execute(text(
                    "SELECT id, request_sha256, state, execution_token, response_status, "
                    "response_json, stale_after, response_expires_at, "
                    "stale_after <= UTC_TIMESTAMP(6) AS is_stale, "
                    "response_expires_at <= UTC_TIMESTAMP(6) AS is_expired "
                    "FROM agent_turn_idempotency "
                    "WHERE student_id=:sid AND session_id=:session AND key_sha256=:key_hash "
                    "FOR UPDATE"
                ), params).mappings().first()
                if row is None:
                    raise TurnIdempotencyStorageUnavailable("幂等记录写入后不可见")
                if row["request_sha256"] != request_sha256:
                    raise TurnIdempotencyConflict("同一 Idempotency-Key 已用于不同请求")

                state = str(row["state"])
                if state == "completed":
                    if bool(row["is_expired"]):
                        conn.execute(text(
                            "UPDATE agent_turn_idempotency SET state='expired', "
                            "response_json=NULL, response_status=NULL "
                            "WHERE id=:id AND state='completed'"
                        ), {"id": row["id"]})
                        # Raising inside ``engine.begin`` would roll this
                        # tombstone transition back.  Defer the public error
                        # until the transaction has committed.
                        post_commit_error = TurnIdempotencyExpired(
                            "幂等结果已过保留期"
                        )
                    else:
                        try:
                            response = json.loads(row["response_json"] or "")
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise TurnIdempotencyStorageUnavailable("幂等结果损坏") from exc
                        if not isinstance(response, dict):
                            raise TurnIdempotencyStorageUnavailable("幂等结果格式损坏")
                        return TurnAdmission(
                            replay_response=response,
                            replay_status=int(row["response_status"] or 200),
                        )
                elif state in {"reserved", "running"}:
                    if bool(row["is_stale"]):
                        conn.execute(text(
                            "UPDATE agent_turn_idempotency SET state='unknown' "
                            "WHERE id=:id AND state IN ('reserved','running')"
                        ), {"id": row["id"]})
                        post_commit_error = TurnIdempotencyUnknown(
                            "先前执行已超时，结果未知"
                        )
                    else:
                        raise TurnIdempotencyInProgress("同一请求仍在处理中")
                elif state == "unknown":
                    raise TurnIdempotencyUnknown("先前执行结果未知")
                elif state == "expired":
                    raise TurnIdempotencyExpired("幂等结果已过保留期")
                elif state != "completed":
                    raise TurnIdempotencyStorageUnavailable("幂等记录状态无效")
            if post_commit_error is not None:
                raise post_commit_error
        except TurnIdempotencyError:
            raise
        except Exception as exc:
            raise TurnIdempotencyStorageUnavailable("幂等存储不可用") from exc

    def start(self, claim: TurnClaim) -> None:
        """CAS a route reservation to running immediately before STM/model work."""
        self._transition(
            claim,
            "UPDATE agent_turn_idempotency SET state='running', "
            "stale_after=TIMESTAMPADD(SECOND, :stale_seconds, UTC_TIMESTAMP(6)) "
            "WHERE student_id=:sid AND session_id=:session AND key_sha256=:key_hash "
            "AND request_sha256=:request_hash AND execution_token=:token AND state='reserved'",
            extra={
                "stale_seconds": self.stale_seconds,
            },
            failure="幂等执行准入已丢失",
        )

    def complete(self, claim: TurnClaim, *, response: Mapping[str, Any],
                 http_status: int = 200) -> None:
        try:
            encoded = json.dumps(
                dict(response),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise TurnIdempotencySettlementLost("公开回合不可持久化") from exc
        self._transition(
            claim,
            "UPDATE agent_turn_idempotency SET state='completed', response_status=:status, "
            "response_json=:response, completed_at=UTC_TIMESTAMP(6), "
            "response_expires_at=TIMESTAMPADD(DAY, :retention_days, UTC_TIMESTAMP(6)) "
            "WHERE student_id=:sid AND session_id=:session AND key_sha256=:key_hash "
            "AND request_sha256=:request_hash AND execution_token=:token AND state='running'",
            extra={
                "status": int(http_status),
                "response": encoded,
                # The replay window begins at reliable completion, not at
                # admission; long-running turns must not lose their window.
                "retention_days": self.retention_days,
            },
            failure="幂等结果结算已丢失",
        )

    def mark_unknown(self, claim: TurnClaim) -> None:
        """Best-effort running/reserved -> unknown; never overwrite completed."""
        params = self._claim_params(claim)
        try:
            with utc_transaction(self.mysql) as conn:
                conn.execute(text(
                    "UPDATE agent_turn_idempotency SET state='unknown' "
                    "WHERE student_id=:sid AND session_id=:session AND key_sha256=:key_hash "
                    "AND request_sha256=:request_hash AND execution_token=:token "
                    "AND state IN ('reserved','running')"
                ), params)
        except Exception as exc:
            # The original error remains authoritative to the caller.  A
            # failed unknown marker leaves reserved/running, both of which are
            # still fail-closed and never trigger automatic re-execution.
            logger.warning(
                "幂等 unknown 标记写入失败；记录将继续 fail-closed (error_type=%s)",
                type(exc).__name__,
            )
            return

    def expire_completed_responses(self, *, limit: int = 500) -> int:
        """Drop old response bodies while retaining permanent no-replay tombstones."""
        try:
            with utc_transaction(self.mysql) as conn:
                result = conn.execute(text(
                    "UPDATE agent_turn_idempotency SET state='expired', response_json=NULL, "
                    "response_status=NULL WHERE state='completed' "
                    "AND response_expires_at <= UTC_TIMESTAMP(6) LIMIT :limit"
                ), {"limit": max(1, min(int(limit), 5000))})
                return int(result.rowcount or 0)
        except Exception as exc:
            raise TurnIdempotencyStorageUnavailable("幂等结果清理失败") from exc

    def _transition(self, claim: TurnClaim, statement: str, *,
                    extra: Optional[dict[str, Any]] = None, failure: str) -> None:
        params = self._claim_params(claim)
        params.update(extra or {})
        try:
            with utc_transaction(self.mysql) as conn:
                result = conn.execute(text(statement), params)
                if result.rowcount != 1:
                    raise TurnIdempotencySettlementLost(failure)
        except TurnIdempotencySettlementLost:
            raise
        except Exception as exc:
            raise TurnIdempotencyStorageUnavailable(failure) from exc

    @staticmethod
    def _claim_params(claim: TurnClaim) -> dict[str, Any]:
        return {
            "sid": claim.student_id,
            "session": claim.session_id,
            "key_hash": claim.key_sha256,
            "request_hash": claim.request_sha256,
            "token": claim.execution_token,
        }


_ledger: Optional[TurnIdempotencyLedger] = None
_ledger_lock = threading.Lock()


def get_turn_idempotency_ledger() -> TurnIdempotencyLedger:
    global _ledger
    if _ledger is None:
        with _ledger_lock:
            if _ledger is None:
                _ledger = TurnIdempotencyLedger()
    return _ledger
