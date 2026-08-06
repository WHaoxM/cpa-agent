-- 章节视频外链（业务数据仅在 SQL，不进 Python）
USE cpa_agent;
SET NAMES utf8mb4;

-- 兼容已有库：无列则加列（MySQL 8+ 无 IF NOT EXISTS 时用存储过程更稳；此处简单尝试）
-- 若列已存在，下面 ALTER 会失败，可忽略后继续 UPDATE。

ALTER TABLE chapter ADD COLUMN video_url VARCHAR(500) DEFAULT NULL COMMENT '章节视频外链';
ALTER TABLE chapter ADD COLUMN video_provider VARCHAR(64) DEFAULT NULL COMMENT '视频提供方标识';

UPDATE chapter SET video_url = 'https://www.bilibili.com/video/BV1Zy4y1K7SH/', video_provider = 'bilibili'
WHERE course_id = 'course_001' AND sequence = 1 AND (video_url IS NULL OR video_url = '');

UPDATE chapter SET video_url = 'https://www.bilibili.com/video/BV1Zy4y1K7SH/', video_provider = 'bilibili'
WHERE course_id = 'course_001' AND sequence = 2 AND (video_url IS NULL OR video_url = '');

UPDATE chapter SET video_url = 'https://www.bilibili.com/video/BV1Zy4y1K7SH/', video_provider = 'bilibili'
WHERE course_id = 'course_001' AND sequence = 3 AND (video_url IS NULL OR video_url = '');
