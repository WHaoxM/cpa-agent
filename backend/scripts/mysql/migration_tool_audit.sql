USE cpa_agent;
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS tool_audit_event (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_id        VARCHAR(64)  NOT NULL UNIQUE,
    session_id      VARCHAR(64)  NOT NULL DEFAULT '',
    student_id      VARCHAR(64)  DEFAULT NULL,
    tool_name       VARCHAR(128) NOT NULL,
    decision        VARCHAR(32)  NOT NULL COMMENT 'allow/deny/ask',
    args_digest     VARCHAR(64)  DEFAULT NULL,
    result_digest   VARCHAR(64)  DEFAULT NULL,
    reason          VARCHAR(512) DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session_time (session_id, created_at DESC),
    INDEX idx_student_time (student_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 工具审计事件';
