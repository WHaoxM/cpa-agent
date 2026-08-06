-- 站内消息种子（与 cpa-xingtu fixtures/home/messages.list.json 对齐）
USE cpa_agent;
SET NAMES utf8mb4;

INSERT IGNORE INTO inbox_message (id, title, body, type, created_at) VALUES
('msg_001', '报告已生成', 'report_001 已可在「我的报告」中查看', '系统消息', '2026-07-18 10:00:00'),
('msg_002', '学习进度提醒', 'Vue 3 入门课程进度 100%，可以开始下一门了', '系统消息', '2026-07-17 14:12:00'),
('msg_003', '职业分析功能升级', '新增薪资趋势与城市热力对比', '系统消息', '2026-07-10 16:45:00');
