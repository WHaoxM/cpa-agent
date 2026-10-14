"""Explicit, additive MySQL migration for account-wide cookie revocation.

DDL commits independently in MySQL. A half-applied migration is not usable:
the auth adapter checks both the column and trigger before accepting cookies.
"""

from __future__ import annotations

from .db import get_mysql_client

TRIGGER_NAME = "cpa_user_account_auth_epoch_v1"

_CREATE_TRIGGER = f"""
CREATE TRIGGER {TRIGGER_NAME}
BEFORE UPDATE ON user_account FOR EACH ROW
BEGIN
    IF NOT (NEW.status <=> OLD.status)
       OR NOT (NEW.role <=> OLD.role)
       OR NOT (NEW.password_hash <=> OLD.password_hash) THEN
        SET NEW.auth_epoch = OLD.auth_epoch + 1;
    ELSE
        SET NEW.auth_epoch = OLD.auth_epoch;
    END IF;
END
"""


def _column_exists(mysql) -> bool:
    return mysql.execute_single(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_account' "
        "AND COLUMN_NAME = 'auth_epoch'"
    ) is not None


def _trigger_exists(mysql) -> bool:
    row = mysql.execute_single(
        "SELECT TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
        "FROM INFORMATION_SCHEMA.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE() "
        "AND EVENT_OBJECT_TABLE = 'user_account' AND TRIGGER_NAME = :name",
        {"name": TRIGGER_NAME},
    )
    return bool(
        row and row["ACTION_TIMING"] == "BEFORE"
        and row["EVENT_MANIPULATION"] == "UPDATE"
        and all(token in row["ACTION_STATEMENT"].lower()
                for token in ("new.auth_epoch", "old.auth_epoch",
                              "new.status", "new.role", "new.password_hash"))
    )


def account_session_schema_ready(mysql=None) -> bool:
    """Readiness requires TRIGGER metadata visibility by the app DB user."""
    mysql = mysql or get_mysql_client()
    return _column_exists(mysql) and _trigger_exists(mysql)


def migrate_account_session_schema(mysql=None) -> None:
    """Idempotent additive migration; never DROP or rewrite existing accounts."""
    mysql = mysql or get_mysql_client()
    if not _column_exists(mysql):
        mysql.execute(
            "ALTER TABLE user_account ADD COLUMN auth_epoch BIGINT UNSIGNED "
            "NOT NULL DEFAULT 0 COMMENT '登录会话失效代数' AFTER status",
            commit=True,
        )
    if not _trigger_exists(mysql):
        mysql.execute(_CREATE_TRIGGER, commit=True)
    if not account_session_schema_ready(mysql):
        raise RuntimeError("account session epoch migration did not verify")
