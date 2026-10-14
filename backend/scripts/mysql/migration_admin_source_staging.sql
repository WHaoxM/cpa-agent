-- Apply once to an existing MySQL volume before enabling multipart admin source staging.
-- These are private source files only. job_dataset.count remains the number of
-- authoritative job rows, which this migration and the staging API do not create.
ALTER TABLE knowledge_base
    ADD COLUMN content_sha256 CHAR(64) DEFAULT NULL COMMENT '私有来源文件 SHA-256' AFTER file_size;

ALTER TABLE job_dataset
    ADD COLUMN content_sha256 CHAR(64) DEFAULT NULL COMMENT '私有来源文件 SHA-256' AFTER file_size,
    ADD COLUMN staged_record_count INT NOT NULL DEFAULT 0 COMMENT '暂存 JSON 条目数；不等于已发布岗位数' AFTER content_sha256;
