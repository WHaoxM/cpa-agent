-- Explicit additive migration. Apply before exposing /api/pipeline/entity-review.
-- The journal does not mutate confirmed resumes, skills, or either graph partition.
CREATE TABLE IF NOT EXISTS resume_entity_review (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    student_id VARCHAR(64) NOT NULL,
    source_confirmation_task_id VARCHAR(64) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    revision INT NOT NULL,
    reviews_json JSON NOT NULL,
    snapshot_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uk_entity_review_request (student_id, request_id),
    UNIQUE KEY uk_entity_review_revision (student_id, source_confirmation_task_id, revision),
    KEY idx_entity_review_source (student_id, source_confirmation_task_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
