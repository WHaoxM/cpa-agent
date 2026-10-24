"""Add revisioned account profile writes and notification preferences."""

from alembic import op
from sqlalchemy import inspect, text


revision = "0022_account_profile_preferences"
down_revision = "0021_agent_task_contract"
branch_labels = None
depends_on = None


_PREFERENCES_DDL = """CREATE TABLE IF NOT EXISTS user_account_notification_preference (
    user_id VARCHAR(64) NOT NULL,
    email_preference BOOLEAN DEFAULT NULL,
    system_preference BOOLEAN DEFAULT NULL,
    sms_preference BOOLEAN DEFAULT NULL,
    revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id),
    CONSTRAINT fk_user_account_notification_preference_user
        FOREIGN KEY (user_id) REFERENCES user_account(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def upgrade() -> None:
    connection = op.get_bind()
    columns = {item["name"] for item in inspect(connection).get_columns("user_account")}
    if "profile_revision" not in columns:
        connection.execute(text(
            "ALTER TABLE user_account ADD COLUMN profile_revision "
            "BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '个人资料并发版本' "
            "AFTER auth_epoch"
        ))

    connection.execute(text(_PREFERENCES_DDL))

    columns = {item["name"] for item in inspect(connection).get_columns("user_account")}
    if "profile_revision" not in columns:
        raise RuntimeError("user_account missing profile_revision")

    preference_columns = {
        item["name"] for item in inspect(connection).get_columns(
            "user_account_notification_preference"
        )
    }
    required = {
        "user_id", "email_preference", "system_preference", "sms_preference",
        "revision", "created_at", "updated_at",
    }
    if missing := required - preference_columns:
        raise RuntimeError(
            "user_account_notification_preference missing columns: "
            f"{sorted(missing)}"
        )
    foreign_keys = {
        item.get("name") for item in inspect(connection).get_foreign_keys(
            "user_account_notification_preference"
        )
    }
    if "fk_user_account_notification_preference_user" not in foreign_keys:
        raise RuntimeError(
            "user_account_notification_preference missing account ownership foreign key"
        )


def downgrade() -> None:
    raise RuntimeError(
        "Account profile and preference data are not automatically downgraded"
    )
