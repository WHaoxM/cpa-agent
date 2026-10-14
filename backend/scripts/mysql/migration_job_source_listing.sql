-- Independent informational source-only catalog. Does not grant L3/L4 scoring.
CREATE TABLE IF NOT EXISTS job_catalog_source_listing (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_record_id VARCHAR(64) NOT NULL,
    listing_version VARCHAR(64) NOT NULL,
    snapshot_json JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'listed',
    listed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_source_listing_version (source_record_id, listing_version),
    INDEX idx_source_listing_latest (source_record_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
