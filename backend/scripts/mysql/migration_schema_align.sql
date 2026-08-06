-- Migration: align live DB with backend/init_mysql.sql (safe to re-run)
USE cpa_agent;
SET NAMES utf8mb4;

-- Memory store (agent awareness)
CREATE TABLE IF NOT EXISTS memory_store (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    store_id        VARCHAR(128) NOT NULL UNIQUE COMMENT 'student:{student_id}',
    student_id      VARCHAR(64)  NOT NULL COMMENT '学生ID',
    status          VARCHAR(20)  NOT NULL DEFAULT 'active' COMMENT 'active/archived',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生记忆 Store';

CREATE TABLE IF NOT EXISTS memory_entry (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    entry_id        VARCHAR(64)  NOT NULL UNIQUE COMMENT 'mem_xxx',
    store_id        VARCHAR(128) NOT NULL COMMENT '→ memory_store.store_id',
    path            VARCHAR(255) NOT NULL COMMENT 'USER.md / MEMORY.md / memory/session-*.md',
    body_md         MEDIUMTEXT   NOT NULL COMMENT 'Markdown body',
    version         INT          NOT NULL DEFAULT 1,
    is_current      TINYINT(1)   NOT NULL DEFAULT 1,
    source          VARCHAR(64)  DEFAULT NULL COMMENT 'user_confirm/reflection_*/rollback/system',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_store_path (store_id, path),
    INDEX idx_current (store_id, path, is_current)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='记忆条目（当前版本）';

CREATE TABLE IF NOT EXISTS memory_entry_version (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    entry_id        VARCHAR(64)  NOT NULL COMMENT '被快照的 entry',
    store_id        VARCHAR(128) NOT NULL,
    path            VARCHAR(255) NOT NULL,
    body_md         MEDIUMTEXT   NOT NULL,
    version         INT          NOT NULL,
    source          VARCHAR(64)  DEFAULT 'snapshot',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_store_path_ver (store_id, path, version DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='记忆版本快照（rollback）';

-- Inbox (messages API) — matches init_mysql.sql / messages_repo DDL
CREATE TABLE IF NOT EXISTS inbox_message (
    id          VARCHAR(64)  NOT NULL PRIMARY KEY,
    title       VARCHAR(200) NOT NULL COMMENT '标题',
    body        TEXT         NOT NULL COMMENT '正文',
    type        VARCHAR(64)  NOT NULL DEFAULT '系统消息' COMMENT '消息类型',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='运营公告/站内消息';

CREATE TABLE IF NOT EXISTS inbox_message_user_state (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     VARCHAR(64)  NOT NULL COMMENT '用户ID',
    message_id  VARCHAR(64)  NOT NULL COMMENT '消息ID',
    is_read     TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否已读',
    is_deleted  TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否对该用户隐藏',
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_msg (user_id, message_id),
    INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='消息按用户已读/删除状态';
