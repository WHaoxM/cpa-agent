-- Operator-controlled publication boundary. Never backfill legacy/mock rows.
CREATE TABLE IF NOT EXISTS job_catalog_publication (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_portrait_id VARCHAR(64) NOT NULL,
    publication_version VARCHAR(64) NOT NULL,
    snapshot_json JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'published',
    published_at DATETIME NOT NULL,
    UNIQUE KEY uk_job_publication (job_portrait_id, publication_version),
    INDEX idx_job_publication (job_portrait_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
