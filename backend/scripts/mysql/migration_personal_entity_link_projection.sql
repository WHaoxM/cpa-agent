-- Durable acknowledgement of each reviewed-source revision. Apply explicitly.
CREATE TABLE IF NOT EXISTS personal_entity_link_projection (
    student_id VARCHAR(64) NOT NULL,
    source_task_id VARCHAR(64) NOT NULL,
    review_revision INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    payload_json JSON NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (student_id, source_task_id, review_revision),
    KEY idx_entity_link_retry (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
