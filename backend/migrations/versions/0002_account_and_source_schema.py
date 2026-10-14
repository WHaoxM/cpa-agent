"""Adopt existing account/session and administrator source schema additions."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0002_account_and_source_schema"
down_revision = "0001_legacy_schema_baseline"
branch_labels = None
depends_on = None

_TRIGGER = """CREATE TRIGGER cpa_user_account_auth_epoch_v1
BEFORE UPDATE ON user_account FOR EACH ROW
BEGIN
    IF NOT (NEW.status <=> OLD.status)
       OR NOT (NEW.role <=> OLD.role)
       OR NOT (NEW.password_hash <=> OLD.password_hash) THEN
        SET NEW.auth_epoch = OLD.auth_epoch + 1;
    ELSE
        SET NEW.auth_epoch = OLD.auth_epoch;
    END IF;
END"""

_SOURCE_LISTING = """CREATE TABLE IF NOT EXISTS job_catalog_source_listing (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_record_id VARCHAR(64) NOT NULL,
    listing_version VARCHAR(64) NOT NULL,
    snapshot_json JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'listed',
    listed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_source_listing_version (source_record_id, listing_version),
    INDEX idx_source_listing_latest (source_record_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def _add_column_if_missing(connection, table, column, ddl):
    if column not in {item["name"] for item in inspect(connection).get_columns(table)}:
        connection.execute(text(f"ALTER TABLE `{table}` ADD COLUMN {ddl}"))


def upgrade() -> None:
    connection = op.get_bind()
    _add_column_if_missing(connection, "user_account", "auth_epoch",
                           "auth_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0 "
                           "COMMENT '登录会话失效代数' AFTER status")
    _add_column_if_missing(connection, "knowledge_base", "content_sha256",
                           "content_sha256 CHAR(64) DEFAULT NULL "
                           "COMMENT '私有来源文件 SHA-256' AFTER file_size")
    _add_column_if_missing(connection, "job_dataset", "content_sha256",
                           "content_sha256 CHAR(64) DEFAULT NULL "
                           "COMMENT '私有来源文件 SHA-256' AFTER file_size")
    _add_column_if_missing(connection, "job_dataset", "staged_record_count",
                           "staged_record_count INT NOT NULL DEFAULT 0 "
                           "COMMENT '暂存 JSON 条目数；不等于已发布岗位数' AFTER content_sha256")
    connection.execute(text(_SOURCE_LISTING))
    triggers = {row[0] for row in connection.execute(text(
        "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
        "WHERE TRIGGER_SCHEMA = DATABASE()"))}
    if "cpa_user_account_auth_epoch_v1" not in triggers:
        connection.execute(text(_TRIGGER))
    trigger = connection.execute(text(
        "SELECT ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE() "
        "AND TRIGGER_NAME = 'cpa_user_account_auth_epoch_v1'"
    )).mappings().first()
    if not (trigger and trigger["ACTION_TIMING"] == "BEFORE"
            and trigger["EVENT_MANIPULATION"] == "UPDATE"
            and all(token in trigger["ACTION_STATEMENT"].lower()
                    for token in ("new.auth_epoch", "old.auth_epoch",
                                  "new.status", "new.role", "new.password_hash"))):
        raise RuntimeError("Account epoch trigger is absent or incompatible")


def downgrade() -> None:
    raise RuntimeError("Account/session and source data are not automatically downgraded")
