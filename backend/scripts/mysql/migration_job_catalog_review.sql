-- Independent model audit; never backfills or approves publications.
CREATE TABLE IF NOT EXISTS job_catalog_review (
    review_id VARCHAR(64) PRIMARY KEY,
    source_record_id VARCHAR(64) NOT NULL,
    review_version VARCHAR(64) NOT NULL,
    actor VARCHAR(20) NOT NULL,
    status VARCHAR(24) NOT NULL,
    review_json JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_job_review_source (source_record_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
