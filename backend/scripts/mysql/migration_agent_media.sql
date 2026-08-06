USE cpa_agent;
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS agent_file (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_id         VARCHAR(64)  NOT NULL UNIQUE,
    student_id      VARCHAR(64)  DEFAULT NULL,
    session_id      VARCHAR(64)  DEFAULT NULL,
    filename        VARCHAR(255) NOT NULL,
    mime_type       VARCHAR(128) DEFAULT NULL,
    size_bytes      BIGINT       NOT NULL DEFAULT 0,
    storage_path    VARCHAR(512) NOT NULL,
    status          VARCHAR(32)  NOT NULL DEFAULT 'uploading',
    purpose         VARCHAR(64)  DEFAULT 'chat_attachment',
    downloadable    TINYINT(1)   NOT NULL DEFAULT 0,
    error           VARCHAR(512) DEFAULT NULL,
    ingest_meta_json JSON        DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_student (student_id),
    INDEX idx_session (session_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_file_artifact (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    artifact_id     VARCHAR(64)  NOT NULL UNIQUE,
    file_id         VARCHAR(64)  NOT NULL,
    kind            VARCHAR(32)  NOT NULL,
    ordinal         INT          NOT NULL DEFAULT 0,
    body_text       MEDIUMTEXT   NOT NULL,
    meta_json       JSON         DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_file_kind (file_id, kind, ordinal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS session_resource (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    resource_id     VARCHAR(64)  NOT NULL UNIQUE,
    session_id      VARCHAR(64)  NOT NULL,
    file_id         VARCHAR(64)  NOT NULL,
    resource_type   VARCHAR(32)  NOT NULL DEFAULT 'file',
    mount_path      VARCHAR(512) NOT NULL,
    meta_json       JSON         DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_session_file (session_id, file_id),
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS voice_turn (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    turn_id         VARCHAR(64)  NOT NULL UNIQUE,
    session_id      VARCHAR(64)  NOT NULL DEFAULT '',
    student_id      VARCHAR(64)  DEFAULT NULL,
    status          VARCHAR(32)  NOT NULL,
    stt_provider    VARCHAR(64)  DEFAULT 'browser_web_speech',
    lang            VARCHAR(16)  DEFAULT 'zh-CN',
    transcript      TEXT,
    interim_transcript TEXT,
    confidence      FLOAT        DEFAULT NULL,
    audio_file_id   VARCHAR(64)  DEFAULT NULL,
    error           VARCHAR(512) DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_session (session_id),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
