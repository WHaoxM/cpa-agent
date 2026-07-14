
USE cpa_agent;

-- ============================================================
-- 一、职业方向摘要(用于 v_career_skill_map)
-- ============================================================
INSERT IGNORE INTO career_insights (role_name, category, sub_category, avg_salary_low, avg_salary_high, demand_level, hot_cities, hot_industries, entry_difficulty, growth_trend, core_skills, skill_count, description, day_in_life) VALUES
('前端开发', '前端', 'Web前端', 12, 24, '高', JSON_ARRAY('上海','杭州','深圳','北京'), JSON_ARRAY('互联网','企业服务','新消费'), '中', '上升', JSON_ARRAY('Vue 3','React','TypeScript','工程化','性能优化','组件库','可视化'), 7, '前端工程师负责 Web 端产品开发,需要掌握现代前端框架和工程化工具。', '参与产品需求评审、写前端代码、做 Code Review、优化性能'),
('后端开发', '后端', '服务端', 14, 26, '高', JSON_ARRAY('北京','深圳','杭州','成都'), JSON_ARRAY('互联网','金融科技','企业平台'), '中', '上升', JSON_ARRAY('Java','Spring Boot','MySQL','Redis','消息队列','Docker','服务治理'), 7, '后端工程师负责服务端系统设计与开发,需要掌握分布式、高并发等核心能力。', '写接口、排查线上问题、设计系统架构'),
('测试开发', '测试', '质量工程', 11, 20, '中', JSON_ARRAY('深圳','上海','苏州','北京'), JSON_ARRAY('互联网','智能硬件','游戏'), '低', '上升', JSON_ARRAY('Python 自动化','Playwright','接口测试','测试用例设计','CI/CD','质量体系','缺陷分析'), 7, '测试开发工程师负责自动化测试框架与质量体系建设。', '写自动化脚本、设计测试用例、分析缺陷'),
('数据分析', '数据', '业务分析', 12, 22, '高', JSON_ARRAY('杭州','上海','北京','广州'), JSON_ARRAY('电商','本地生活','内容平台'), '中', '上升', JSON_ARRAY('Python','SQL','统计分析','BI 工具','指标体系','A/B 测试','数据清洗'), 7, '数据分析师通过数据驱动业务决策,需要统计分析与业务理解能力。', '写 SQL、跑分析、写报告'),
('机器学习工程师', 'AI', '算法工程', 18, 32, '高', JSON_ARRAY('北京','上海','深圳','杭州'), JSON_ARRAY('人工智能','自动驾驶','医疗科技'), '高', '上升', JSON_ARRAY('Python','机器学习','PyTorch','特征工程','模型部署','数据处理','MLOps'), 7, '机器学习工程师将算法落地到产品,需要算法基础与工程能力并重。', '训练模型、优化性能、上线部署'),
('Python 后端开发工程师', '后端', '服务端', 15, 28, '高', JSON_ARRAY('北京','上海','杭州','深圳'), JSON_ARRAY('互联网','AI 平台','数据服务'), '中', '上升', JSON_ARRAY('Python','FastAPI','Django','MySQL','Redis','Docker','Kubernetes'), 7, 'Python 后端工程师使用 Python 生态构建服务端应用。', '开发 API、优化性能、处理数据');

-- ============================================================
-- 二、技能分类树(用于 v_career_skill_map)
-- ============================================================
INSERT IGNORE INTO skill_taxonomy (skill_name, category, sub_category, tier, difficulty, weeks_to_learn, prerequisites, related_roles, heat_score, dimension, proficiency_criteria, description) VALUES
('Vue 3', '前端框架', 'Vue', 1, '中', 8, JSON_ARRAY('JavaScript','HTML','CSS'), JSON_ARRAY('前端开发'), 0.95, 'D2', JSON_OBJECT('精通', '能独立搭建完整项目', '熟练', '能完成日常开发', '了解', '能看懂代码', '不会', '完全没接触过'), 'Vue 3 是渐进式 JavaScript 框架。'),
('React', '前端框架', 'React', 1, '中', 10, JSON_ARRAY('JavaScript','ES6'), JSON_ARRAY('前端开发'), 0.92, 'D2', JSON_OBJECT('精通', '能独立搭建完整项目', '熟练', '能完成日常开发', '了解', '能看懂代码', '不会', '完全没接触过'), 'React 是 Facebook 推出的用户界面库。'),
('TypeScript', '前端语言', 'TypeScript', 2, '中', 6, JSON_ARRAY('JavaScript'), JSON_ARRAY('前端开发','全栈开发'), 0.90, 'D2', JSON_OBJECT('精通', '能用 TS 写大型项目', '熟练', '能在项目中用 TS', '了解', '能看懂 TS 代码', '不会', '完全没接触过'), 'TypeScript 是 JavaScript 的超集。'),
('Node.js', '后端语言', 'Node.js', 2, '中', 6, JSON_ARRAY('JavaScript'), JSON_ARRAY('后端开发','全栈开发'), 0.85, 'D2', JSON_OBJECT('精通', '能独立搭建完整服务', '熟练', '能完成日常开发', '了解', '能看懂代码', '不会', '完全没接触过'), 'Node.js 是基于 Chrome V8 引擎的 JavaScript 运行时。'),
('Python', 'AI 语言', 'Python', 1, '低', 6, JSON_ARRAY(), JSON_ARRAY('后端开发','数据分析','AI'), 0.95, 'D2', JSON_OBJECT('精通', '能独立搭建完整服务', '熟练', '能完成日常开发', '了解', '能看懂代码', '不会', '完全没接触过'), 'Python 是高级动态语言。'),
('MySQL', '数据库', '关系型数据库', 1, '中', 4, JSON_ARRAY('SQL 基础'), JSON_ARRAY('后端开发','数据分析'), 0.88, 'D2', JSON_OBJECT('精通', '能设计复杂库结构', '熟练', '能完成日常开发', '了解', '能写简单 SQL', '不会', '完全没接触过'), 'MySQL 是最流行的开源关系型数据库。'),
('Java', '后端语言', 'Java', 2, '中', 12, JSON_ARRAY('OOP'), JSON_ARRAY('后端开发'), 0.88, 'D2', JSON_OBJECT('精通', '能独立搭建完整服务', '熟练', '能完成日常开发', '了解', '能看懂代码', '不会', '完全没接触过'), 'Java 是广泛使用的企业级后端开发语言。'),
('Redis', '数据库', '缓存', 2, '中', 3, JSON_ARRAY('MySQL'), JSON_ARRAY('后端开发'), 0.80, 'D2', JSON_OBJECT('精通', '能用 Redis 设计高并发系统', '熟练', '能用 Redis 做缓存', '了解', '知道 Redis 概念', '不会', '完全没接触过'), 'Redis 是高性能内存数据库。'),
('Docker', '运维', '容器化', 3, '中', 3, JSON_ARRAY('Linux'), JSON_ARRAY('后端开发','运维'), 0.78, 'D2', JSON_OBJECT('精通', '能设计容器化架构', '熟练', '能使用 Docker 部署应用', '了解', '知道 Docker 概念', '不会', '完全没接触过'), 'Docker 是应用容器引擎。'),
('性能优化', '前端工程', '高级', 3, '高', 8, JSON_ARRAY('Vue 3','React','浏览器原理'), JSON_ARRAY('前端开发'), 0.75, 'D2', JSON_OBJECT('精通', '能独立完成性能调优', '熟练', '能做基本优化', '了解', '知道优化思路', '不会', '完全没接触过'), '前端性能优化是高级前端工程师核心能力。'),
('组件库', '前端工程', '高级', 3, '高', 6, JSON_ARRAY('Vue 3','React','TypeScript'), JSON_ARRAY('前端开发'), 0.72, 'D2', JSON_OBJECT('精通', '能独立设计组件库', '熟练', '能封装业务组件', '了解', '会用第三方组件库', '不会', '完全没接触过'), '组件库设计是高级前端工程师的标志性能力。'),
('Spring Boot', '后端框架', 'Java', 2, '中', 8, JSON_ARRAY('Java'), JSON_ARRAY('后端开发'), 0.85, 'D2', JSON_OBJECT('精通', '能独立搭建微服务', '熟练', '能用 Spring Boot 开发', '了解', '能看懂代码', '不会', '完全没接触过'), 'Spring Boot 是 Java 生态最主流的应用框架。'),
('机器学习', 'AI', '算法', 3, '高', 12, JSON_ARRAY('Python','数学基础'), JSON_ARRAY('AI'), 0.82, 'D2', JSON_OBJECT('精通', '能独立设计算法方案', '熟练', '能用 sklearn 建模', '了解', '知道 ML 概念', '不会', '完全没接触过'), '机器学习是 AI 的核心技术。'),
('PyTorch', 'AI', '深度学习', 3, '高', 8, JSON_ARRAY('Python','机器学习'), JSON_ARRAY('AI'), 0.78, 'D2', JSON_OBJECT('精通', '能训练生产级模型', '熟练', '能用 PyTorch 建模', '了解', '能看懂代码', '不会', '完全没接触过'), 'PyTorch 是主流深度学习框架。'),
('FastAPI', '后端框架', 'Python', 2, '中', 4, JSON_ARRAY('Python'), JSON_ARRAY('后端开发'), 0.75, 'D2', JSON_OBJECT('精通', '能设计复杂 API 架构', '熟练', '能用 FastAPI 开发', '了解', '能看懂代码', '不会', '完全没接触过'), 'FastAPI 是现代化高性能 Python Web 框架。'),
('SQL', '数据库', '基础', 1, '低', 4, JSON_ARRAY(), JSON_ARRAY('后端开发','数据分析'), 0.95, 'D2', JSON_OBJECT('精通', '能写复杂查询', '熟练', '能完成日常开发', '了解', '能写简单查询', '不会', '完全没接触过'), 'SQL 是结构化查询语言。'),
('Playwright', '测试', '自动化', 2, '中', 3, JSON_ARRAY('JavaScript','Python'), JSON_ARRAY('测试开发'), 0.70, 'D2', JSON_OBJECT('精通', '能搭建 E2E 框架', '熟练', '能用 Playwright 写测试', '了解', '知道 Playwright 概念', '不会', '完全没接触过'), 'Playwright 是新一代 E2E 自动化测试工具。'),
('团队协作', '通用能力', '软技能', 1, '低', 4, JSON_ARRAY(), JSON_ARRAY('所有岗位'), 0.90, 'D3', JSON_OBJECT('精通', '能主导复杂协作', '熟练', '能与他人高效协作', '了解', '能参与团队工作', '不会', '完全没接触过'), '团队协作是所有岗位的基础能力。'),
('沟通表达', '通用能力', '软技能', 1, '低', 4, JSON_ARRAY(), JSON_ARRAY('所有岗位'), 0.92, 'D3', JSON_OBJECT('精通', '能在公开场合自如表达', '熟练', '能清晰表达想法', '了解', '能基本沟通', '不会', '完全没接触过'), '沟通表达是职场核心软技能。'),
('学习能力', '通用能力', '软技能', 1, '低', 4, JSON_ARRAY(), JSON_ARRAY('所有岗位'), 0.95, 'D4', JSON_OBJECT('精通', '能快速掌握新领域', '熟练', '能较快学习新技能', '了解', '能跟上学习节奏', '不会', '完全没接触过'), '学习能力是发展潜力的核心指标。'),
('问题解决', '通用能力', '软技能', 1, '中', 4, JSON_ARRAY(), JSON_ARRAY('所有岗位'), 0.92, 'D3', JSON_OBJECT('精通', '能解决复杂难题', '熟练', '能解决一般问题', '了解', '能在指导下解决问题', '不会', '完全没接触过'), '问题解决能力是工程师核心素养。');

-- ============================================================
-- 三、学习路径模板(用于 v_learning_path_full)
-- ============================================================
INSERT IGNORE INTO learning_path_template (path_id, target_role, target_level, steps, total_weeks, description) VALUES
('path_001', '前端开发工程师', '初级', JSON_ARRAY(JSON_OBJECT('order', 1, 'skill', 'Vue 3', 'weeks', 8, 'resources', JSON_ARRAY('Vue 3 官方文档', '慕课网 Vue 3 实战')), JSON_OBJECT('order', 2, 'skill', 'TypeScript', 'weeks', 6, 'resources', JSON_ARRAY('TypeScript 官方手册')), JSON_OBJECT('order', 3, 'skill', '性能优化', 'weeks', 4, 'resources', JSON_ARRAY('前端性能优化原理与实践'))), 18, '从零开始学习前端开发,目标是掌握 Vue 3 + TypeScript 技术栈'),
('path_002', 'Python 后端开发工程师', '初级', JSON_ARRAY(JSON_OBJECT('order', 1, 'skill', 'Python', 'weeks', 6, 'resources', JSON_ARRAY('Python 官方文档')), JSON_OBJECT('order', 2, 'skill', 'FastAPI', 'weeks', 4, 'resources', JSON_ARRAY('FastAPI 官方文档')), JSON_OBJECT('order', 3, 'skill', 'MySQL', 'weeks', 4, 'resources', JSON_ARRAY('MySQL 必知必会')), JSON_OBJECT('order', 4, 'skill', 'Redis', 'weeks', 3, 'resources', JSON_ARRAY('Redis 设计与实现'))), 17, '使用 Python 生态构建后端服务'),
('path_003', '机器学习工程师', '中级', JSON_ARRAY(JSON_OBJECT('order', 1, 'skill', 'Python', 'weeks', 6, 'resources', JSON_ARRAY('Python 编程快速上手')), JSON_OBJECT('order', 2, 'skill', '机器学习', 'weeks', 12, 'resources', JSON_ARRAY('机器学习实战', '吴恩达 ML 课程')), JSON_OBJECT('order', 3, 'skill', 'PyTorch', 'weeks', 8, 'resources', JSON_ARRAY('PyTorch 官方教程')), JSON_OBJECT('order', 4, 'skill', 'SQL', 'weeks', 4, 'resources', JSON_ARRAY('SQL 必知必会'))), 30, '从零进阶到机器学习工程师'),
('path_004', '测试开发工程师', '初级', JSON_ARRAY(JSON_OBJECT('order', 1, 'skill', 'Python', 'weeks', 6, 'resources', JSON_ARRAY('Python 基础教程')), JSON_OBJECT('order', 2, 'skill', 'Playwright', 'weeks', 3, 'resources', JSON_ARRAY('Playwright 官方文档')), JSON_OBJECT('order', 3, 'skill', 'SQL', 'weeks', 4, 'resources', JSON_ARRAY('SQL 入门'))), 13, '从手工测试转向自动化测试开发');

-- ============================================================
-- 四、岗位画像(让 v_job_match_summary 有更多岗位)
-- ============================================================
INSERT IGNORE INTO job_portrait_4d (portrait_id, position_name, jd_count, required_degree, major_strict, required_skill_cnt, portrait_json) VALUES
('job_001', '前端开发工程师', 150, '本科', 0, 7, JSON_OBJECT('skills', JSON_ARRAY('Vue 3', 'React', 'TypeScript'), 'salary_range', '12K-24K')),
('job_002', '后端开发工程师', 200, '本科', 0, 7, JSON_OBJECT('skills', JSON_ARRAY('Java', 'Spring Boot', 'MySQL'), 'salary_range', '14K-26K')),
('job_003', 'Python 后端开发工程师', 80, '本科', 0, 6, JSON_OBJECT('skills', JSON_ARRAY('Python', 'FastAPI', 'MySQL'), 'salary_range', '15K-28K')),
('job_004', '测试开发工程师', 60, '本科', 0, 5, JSON_OBJECT('skills', JSON_ARRAY('Python', 'Playwright', 'CI/CD'), 'salary_range', '11K-20K'));

-- ============================================================
-- 五、补全学生画像和简历,让所有视图有数据
-- ============================================================
INSERT IGNORE INTO student_profile (student_id, name, phone, email, degree, school, major, graduation_year, profile_json, completeness) VALUES
('teacher_001', '刘老师', '13600136001', 'teacher001@example.com', '硕士', '北京邮电大学', '计算机科学', 2010, JSON_OBJECT('name', '刘老师', 'role', 'teacher'), 1.0),
('admin_001', '管理员', '13700137001', 'admin001@example.com', '本科', '清华大学', '管理学', 2015, JSON_OBJECT('name', '管理员', 'role', 'admin'), 1.0),
('student_004', '赵同学', '13900139001', 'student004@example.com', '本科', '上海交通大学', '计算机科学', 2025, JSON_OBJECT('name', '赵同学'), 0.85),
('student_005', '钱同学', '13900139002', 'student005@example.com', '本科', '浙江大学', '软件工程', 2025, JSON_OBJECT('name', '钱同学'), 0.80);

INSERT IGNORE INTO student_portrait_4d (student_id, target_job_id, d1_score, d2_score, d3_score, d4_score, competitiveness, competitiveness_lvl, completeness, skill_coverage, skill_count, d3_confidence, portrait_json) VALUES
('student_004', 'job_001', 88.0, 70.0, 80.0, 85.0, 79.0, '强', 85.0, 0.70, 20, 0.78, JSON_OBJECT('note', '赵同学基础扎实')),
('student_005', 'job_002', 82.0, 75.0, 70.0, 75.0, 76.4, '强', 80.0, 0.75, 22, 0.72, JSON_OBJECT('note', '钱同学后端基础好'));

INSERT IGNORE INTO match_result (student_id, job_portrait_id, d1_match, d2_match, d3_match, d4_match, total_score, d1_pass, skill_coverage, is_matched, match_level, result_json) VALUES
('student_003', 'job_001', 75.0, 65.0, 72.0, 70.0, 70.4, 1, 0.65, 1, '一般', JSON_OBJECT('note', '王同学需要补齐 Vue 3 技能')),
('student_004', 'job_001', 88.0, 70.0, 80.0, 85.0, 79.0, 1, 0.70, 1, '良好', JSON_OBJECT('note', '赵同学匹配良好')),
('student_005', 'job_002', 82.0, 75.0, 70.0, 75.0, 76.4, 1, 0.75, 1, '良好', JSON_OBJECT('note', '钱同学匹配良好'));

-- ============================================================
-- 六、修正章节 ID 与 chapter 表对齐
-- ============================================================
UPDATE learning_progress SET chapter_id = '1' WHERE user_id = 'student_001' AND course_id = 'course_001' AND progress = 100 AND chapter_id = 'ch_001';
UPDATE learning_progress SET chapter_id = '2' WHERE user_id = 'student_001' AND course_id = 'course_001' AND progress = 85 AND chapter_id = 'ch_002';
UPDATE learning_progress SET chapter_id = '3' WHERE user_id = 'student_001' AND course_id = 'course_001' AND progress = 45 AND chapter_id = 'ch_003';
UPDATE learning_progress SET chapter_id = '7' WHERE user_id = 'student_001' AND course_id = 'course_002' AND progress = 100 AND chapter_id = 'ch_101';
UPDATE learning_progress SET chapter_id = '8' WHERE user_id = 'student_001' AND course_id = 'course_002' AND progress = 60 AND chapter_id = 'ch_102';
UPDATE learning_progress SET chapter_id = '1' WHERE user_id = 'student_002' AND course_id = 'course_001' AND progress = 100 AND chapter_id = 'ch_001';
UPDATE learning_progress SET chapter_id = '2' WHERE user_id = 'student_002' AND course_id = 'course_001' AND progress = 100 AND chapter_id = 'ch_002';
UPDATE learning_progress SET chapter_id = '3' WHERE user_id = 'student_002' AND course_id = 'course_001' AND progress = 30 AND chapter_id = 'ch_003';
UPDATE learning_progress SET chapter_id = '1' WHERE user_id = 'student_003' AND course_id = 'course_001' AND progress = 100 AND chapter_id = 'ch_001';
UPDATE learning_progress SET chapter_id = '2' WHERE user_id = 'student_003' AND course_id = 'course_001' AND progress = 20 AND chapter_id = 'ch_002';

-- ============================================================
-- 验证
-- ============================================================
SELECT 'career_insights' AS tbl, COUNT(*) AS cnt FROM career_insights
UNION ALL SELECT 'skill_taxonomy', COUNT(*) FROM skill_taxonomy
UNION ALL SELECT 'learning_path_template', COUNT(*) FROM learning_path_template
UNION ALL SELECT 'job_portrait_4d', COUNT(*) FROM job_portrait_4d
UNION ALL SELECT 'student_profile', COUNT(*) FROM student_profile
UNION ALL SELECT 'student_portrait_4d', COUNT(*) FROM student_portrait_4d
UNION ALL SELECT 'match_result', COUNT(*) FROM match_result;
