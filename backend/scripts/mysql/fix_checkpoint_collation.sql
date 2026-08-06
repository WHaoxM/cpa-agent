-- LangGraph MySQL checkpointer：JSON_TABLE 默认 utf8mb4_0900_ai_ci，
-- 与库默认 utf8mb4_unicode_ci 混用会触发 1267 Illegal mix of collations。
-- 见 https://github.com/tjni/langgraph-checkpoint-mysql/issues/55
-- 仅转换 checkpoint 相关表，不影响业务表。

ALTER TABLE checkpoints
  CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

ALTER TABLE checkpoint_blobs
  CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

ALTER TABLE checkpoint_writes
  CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- 若存在 migrations 表也一并对齐
ALTER TABLE checkpoint_migrations
  CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
