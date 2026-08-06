"""
站内消息 / 运营公告数据访问

表:
- inbox_message — 共享公告（业务行仅来自 SQL seed / 运维导入）
- inbox_message_user_state — 按 user_id 的已读/删除

ensure_schema 只做 DDL，不插入任何业务文案。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger("cpa-agent.repo.messages")

_SCHEMA_READY = False

_DDL_MESSAGE = """
CREATE TABLE IF NOT EXISTS inbox_message (
    id          VARCHAR(64)  NOT NULL PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    body        TEXT         NOT NULL,
    type        VARCHAR(64)  NOT NULL DEFAULT '',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='运营公告/站内消息'
"""

_DDL_STATE = """
CREATE TABLE IF NOT EXISTS inbox_message_user_state (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     VARCHAR(64)  NOT NULL,
    message_id  VARCHAR(64)  NOT NULL,
    is_read     TINYINT(1)   NOT NULL DEFAULT 0,
    is_deleted  TINYINT(1)   NOT NULL DEFAULT 0,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_msg (user_id, message_id),
    INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='消息按用户已读/删除状态'
"""


class MessagesRepository:
    def ensure_schema(self) -> None:
        """CREATE IF NOT EXISTS；进程内只跑一次。不插入种子行。"""
        global _SCHEMA_READY
        if _SCHEMA_READY:
            return
        mysql = get_mysql_client()
        mysql.execute_write(_DDL_MESSAGE)
        mysql.execute_write(_DDL_STATE)
        _SCHEMA_READY = True
        logger.info("inbox_message schema ensured (DDL only)")

    def list_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        self.ensure_schema()
        mysql = get_mysql_client()
        return mysql.execute(
            """
            SELECT m.id, m.title, m.body, m.type, m.created_at,
                   COALESCE(s.is_read, 0) AS is_read
            FROM inbox_message m
            LEFT JOIN inbox_message_user_state s
              ON s.message_id = m.id AND s.user_id = :user_id
            WHERE COALESCE(s.is_deleted, 0) = 0
            ORDER BY m.created_at DESC
            """,
            {"user_id": user_id},
        )

    def mark_read(self, *, user_id: str, message_id: str) -> bool:
        self.ensure_schema()
        mysql = get_mysql_client()
        exists = mysql.execute(
            "SELECT id FROM inbox_message WHERE id = :id LIMIT 1",
            {"id": message_id},
        )
        if not exists:
            return False
        mysql.execute_write(
            """
            INSERT INTO inbox_message_user_state (user_id, message_id, is_read, is_deleted)
            VALUES (:user_id, :message_id, 1, 0)
            ON DUPLICATE KEY UPDATE is_read = 1, is_deleted = 0
            """,
            {"user_id": user_id, "message_id": message_id},
        )
        return True

    def soft_delete(self, *, user_id: str, message_id: str) -> bool:
        self.ensure_schema()
        mysql = get_mysql_client()
        exists = mysql.execute(
            "SELECT id FROM inbox_message WHERE id = :id LIMIT 1",
            {"id": message_id},
        )
        if not exists:
            return False
        mysql.execute_write(
            """
            INSERT INTO inbox_message_user_state (user_id, message_id, is_read, is_deleted)
            VALUES (:user_id, :message_id, 1, 1)
            ON DUPLICATE KEY UPDATE is_deleted = 1
            """,
            {"user_id": user_id, "message_id": message_id},
        )
        return True


messages_repo = MessagesRepository()
