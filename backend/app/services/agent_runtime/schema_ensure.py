"""Idempotent DDL for agent runtime tables (audit + media)."""

from __future__ import annotations

from ...utils.logger import get_logger

logger = get_logger("cpa-agent.agent_runtime.schema")

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS tool_audit_event (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_id        VARCHAR(64)  NOT NULL UNIQUE,
    session_id      VARCHAR(64)  NOT NULL DEFAULT '',
    student_id      VARCHAR(64)  DEFAULT NULL,
    tool_name       VARCHAR(128) NOT NULL,
    decision        VARCHAR(32)  NOT NULL,
    args_digest     VARCHAR(64)  DEFAULT NULL,
    result_digest   VARCHAR(64)  DEFAULT NULL,
    reason          VARCHAR(512) DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session_time (session_id, created_at DESC),
    INDEX idx_student_time (student_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_audit_ok = False


def ensure_tool_audit_schema(mysql_client=None) -> bool:
    global _audit_ok
    if _audit_ok:
        return True
    try:
        if mysql_client is None:
            from ..db import get_mysql_client
            mysql_client = get_mysql_client()
        mysql_client.execute(_AUDIT_DDL, commit=True)
        _audit_ok = True
        logger.info("tool_audit_event schema ensured")
        return True
    except Exception as e:
        logger.warning("ensure_tool_audit_schema skipped: %s", e)
        return False


def ensure_runtime_schema() -> None:
    """Best-effort: media tables + audit table."""
    try:
        from .media.schema import ensure_media_schema
        ensure_media_schema()
    except Exception as e:
        logger.warning("ensure_media_schema: %s", e)
    ensure_tool_audit_schema()
