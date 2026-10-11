-- Additive migration for existing CPA-Agent MySQL volumes.
-- New installations also receive this table from init_mysql.sql.
CREATE TABLE IF NOT EXISTS agent_turn_idempotency (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    session_id          VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    key_sha256          CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256      CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    state               VARCHAR(16) NOT NULL COMMENT 'reserved/running/completed/unknown/expired',
    execution_token     CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    response_status     SMALLINT UNSIGNED DEFAULT NULL,
    response_json       MEDIUMTEXT DEFAULT NULL,
    stale_after         DATETIME(6) NOT NULL,
    response_expires_at DATETIME(6) NOT NULL,
    created_at          DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at          DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                      ON UPDATE CURRENT_TIMESTAMP(6),
    completed_at        DATETIME(6) DEFAULT NULL,
    UNIQUE KEY uk_agent_turn_idempotency (student_id, session_id, key_sha256),
    INDEX idx_agent_turn_idempotency_expiry (state, response_expires_at),
    INDEX idx_agent_turn_idempotency_session (student_id, session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='显式 Agent HTTP 幂等准入与公开结果重放';
