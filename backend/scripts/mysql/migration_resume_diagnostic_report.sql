-- Resume-only report. Deliberately separate from job-specific career_report:
-- the latter requires a published job and a verified match.
CREATE TABLE IF NOT EXISTS resume_diagnostic_report (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    report_id VARCHAR(64) NOT NULL UNIQUE,
    student_id VARCHAR(64) NOT NULL,
    source_confirmation_task_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    report_json JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_resume_diagnostic_source (student_id, source_confirmation_task_id),
    INDEX idx_resume_diagnostic_owner (student_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
