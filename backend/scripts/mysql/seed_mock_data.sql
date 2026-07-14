-- ============================================================
-- 冷启动数据(基于前端 mock 数据)
-- 数据源: vue-kecheng/src/mock/data.ts
-- 日期: 2026-07-12
-- 说明: 把前端 mock 数据插到数据库, 让视图查询有数据返回
-- ============================================================

USE cpa_agent;

-- ============================================================
-- 一、班级数据
-- ============================================================
INSERT IGNORE INTO class_info (id, name, teacher_id, course_ids, student_count, description) VALUES
('class_001', '2024 前端开发班', 'teacher_001', JSON_ARRAY('course_001', 'course_002'), 3, '2024级前端开发实训班');

-- ============================================================
-- 二、用户账号(教师/学生/管理员)
-- ============================================================
INSERT IGNORE INTO user_account (id, username, password_hash, name, email, phone, avatar, role, status, class_id, signature) VALUES
('teacher_001', 'teacher001', '$2b$12$placeholder_hash_teacher', '刘老师', 'teacher001@example.com', '13600136001', 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png', 'teacher', 'active', NULL, '资深前端工程师,擅长 Vue/React 全栈开发'),
('admin_001', 'admin001', '$2b$12$placeholder_hash_admin', '管理员', 'admin001@example.com', '13700137001', 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png', 'admin', 'active', NULL, '系统管理员');

-- ============================================================
-- 三、学生账号(从前端 mockUsers 提取)
-- ============================================================
INSERT IGNORE INTO user_account (id, username, password_hash, name, email, phone, avatar, role, status, class_id, signature) VALUES
('student_001', '123456', '$2b$12$placeholder_hash_s1', '张同学', 'student001@example.com', '13800138001', 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png', 'student', 'active', 'class_001', '热爱前端,目标进大厂'),
('student_002', '123456', '$2b$12$placeholder_hash_s2', '李同学', 'student002@example.com', NULL, 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png', 'student', 'active', 'class_001', '正在学习 TypeScript'),
('student_003', '123456', '$2b$12$placeholder_hash_s3', '王同学', 'student003@example.com', NULL, 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png', 'student', 'active', 'class_001', '专注学习');

-- ============================================================
-- 四、课程(从前端 mockCourses 提取)
-- ============================================================
INSERT IGNORE INTO course (course_id, title, category, description, difficulty, duration_hours, instructor, thumbnail_url) VALUES
('course_001', 'Vue 3 前端开发实战', '前端开发', '从零开始学习 Vue 3,掌握现代前端开发技术栈,包括 Composition API、Pinia、Vue Router 等核心技术。', 'beginner', 300, '刘老师', 'https://picsum.photos/400/300?random=1'),
('course_002', 'TypeScript 进阶开发', '前端开发', '深入学习 TypeScript 高级特性,掌握类型体操、泛型编程等进阶技术。', 'intermediate', 185, '刘老师', 'https://picsum.photos/400/300?random=2'),
('course_003', 'Node.js 后端开发', '后端开发', '学习 Node.js 服务端开发,掌握 Express、Koa 等主流框架。', 'intermediate', 195, '刘老师', 'https://picsum.photos/400/300?random=3'),
('course_004', 'Python 人工智能入门', '人工智能', '从零开始学习 Python 和人工智能基础,包括机器学习算法和深度学习框架。', 'beginner', 245, '刘老师', 'https://picsum.photos/400/300?random=4'),
('course_005', 'MySQL 数据库设计与优化', '数据库', '深入学习 MySQL 数据库设计、SQL 优化和高可用架构。', 'intermediate', 170, '刘老师', 'https://picsum.photos/400/300?random=5');

-- ============================================================
-- 五、章节(从前端 mockChapters 提取)
-- ============================================================
INSERT IGNORE INTO chapter (course_id, title, sequence, duration_hours, content_summary) VALUES
('course_001', '课程介绍与环境搭建', 1, 15, 'Vue 3 项目初始化,开发环境配置'),
('course_001', 'Vue 3 基础语法', 2, 45, '模板语法、指令、计算属性'),
('course_001', 'Composition API 详解', 3, 60, '组合式 API 深入应用'),
('course_001', '组件通信与状态管理', 4, 50, 'props/emit/Pinia 状态管理'),
('course_001', '路由与导航', 5, 40, 'Vue Router 4 路由配置'),
('course_001', '项目实战', 6, 90, '完整项目开发流程'),
('course_002', 'TS 基础回顾', 1, 30, 'TypeScript 基础语法回顾'),
('course_002', '高级类型', 2, 45, '联合类型、交叉类型、类型守卫'),
('course_002', '泛型编程', 3, 60, '泛型函数、泛型接口'),
('course_002', '类型体操', 4, 50, '条件类型、映射类型'),
('course_003', 'Node.js 基础', 1, 40, 'Node.js 核心模块'),
('course_003', 'Express 框架', 2, 50, 'Express 路由与中间件'),
('course_003', '数据库操作', 3, 60, 'MySQL/MongoDB 集成'),
('course_003', 'API 设计', 4, 45, 'RESTful API 最佳实践'),
('course_004', 'Python 基础', 1, 50, 'Python 语法基础'),
('course_004', 'NumPy 与 Pandas', 2, 45, '科学计算与数据处理'),
('course_004', '机器学习算法', 3, 70, '监督学习、无监督学习'),
('course_004', '深度学习入门', 4, 80, '神经网络与 TensorFlow'),
('course_005', 'SQL 基础', 1, 35, 'SELECT/INSERT/UPDATE/DELETE'),
('course_005', '数据库设计', 2, 45, '三范式、ER 图设计'),
('course_005', '索引与优化', 3, 50, 'B+ 树、查询优化'),
('course_005', '事务与锁', 4, 40, 'ACID、隔离级别');

-- ============================================================
-- 六、学习进度(从前端 mockLearningProgress 提取)
-- ============================================================
INSERT IGNORE INTO learning_progress (user_id, course_id, chapter_id, progress, completed, last_position, completed_at) VALUES
('student_001', 'course_001', 'ch_001', 100, 1, 900, '2024-02-10 10:00:00'),
('student_001', 'course_001', 'ch_002', 85, 0, 2295, NULL),
('student_001', 'course_001', 'ch_003', 45, 0, 1620, NULL),
('student_001', 'course_002', 'ch_101', 100, 1, 1800, '2024-02-08 09:00:00'),
('student_001', 'course_002', 'ch_102', 60, 0, 1620, NULL),
('student_002', 'course_001', 'ch_001', 100, 1, 900, '2024-02-10 09:00:00'),
('student_002', 'course_001', 'ch_002', 100, 1, 2700, '2024-02-10 15:00:00'),
('student_002', 'course_001', 'ch_003', 30, 0, 1080, NULL),
('student_003', 'course_001', 'ch_001', 100, 1, 900, '2024-02-10 08:00:00'),
('student_003', 'course_001', 'ch_002', 20, 0, 540, NULL);

-- ============================================================
-- 七、学生成绩(从前端 mockQuizRecords 推导)
-- ============================================================
INSERT IGNORE INTO student_grade (user_id, course_id, total_score, quiz_scores, completion_rate, graded_at) VALUES
('student_001', 'course_001', 75, JSON_ARRAY(75), 0.77, '2024-02-10 15:00:00'),
('student_001', 'course_002', 90, JSON_ARRAY(90), 0.50, '2024-02-08 10:00:00'),
('student_002', 'course_001', 85, JSON_ARRAY(85), 0.70, '2024-02-10 16:00:00'),
('student_003', 'course_001', 60, JSON_ARRAY(60), 0.40, '2024-02-10 11:00:00');

-- ============================================================
-- 八、登录日志(用于活跃统计)
-- ============================================================
INSERT IGNORE INTO active_user_log (user_id, login_at, ip, user_agent) VALUES
('student_001', '2026-07-12 09:00:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)'),
('student_001', '2026-07-11 14:30:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)'),
('student_001', '2026-07-10 10:15:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)'),
('student_001', '2026-07-05 16:00:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)'),
('student_002', '2026-07-12 08:30:00', '127.0.0.1', 'Mozilla/5.0 (Macintosh)'),
('student_002', '2026-07-11 13:00:00', '127.0.0.1', 'Mozilla/5.0 (Macintosh)'),
('student_002', '2026-07-09 09:00:00', '127.0.0.1', 'Mozilla/5.0 (Macintosh)'),
('student_003', '2026-07-11 15:00:00', '127.0.0.1', 'Mozilla/5.0 (Linux)'),
('student_003', '2026-07-08 10:30:00', '127.0.0.1', 'Mozilla/5.0 (Linux)'),
('teacher_001', '2026-07-12 08:00:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)'),
('teacher_001', '2026-07-11 08:00:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)'),
('admin_001', '2026-07-12 09:30:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)'),
('admin_001', '2026-07-10 09:30:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)'),
('admin_001', '2026-07-08 09:30:00', '127.0.0.1', 'Mozilla/5.0 (Windows NT 10.0)');

-- 更新 user_account 的 last_login_at
UPDATE user_account SET last_login_at = '2026-07-12 09:00:00' WHERE id = 'student_001';
UPDATE user_account SET last_login_at = '2026-07-12 08:30:00' WHERE id = 'student_002';
UPDATE user_account SET last_login_at = '2026-07-11 15:00:00' WHERE id = 'student_003';
UPDATE user_account SET last_login_at = '2026-07-12 08:00:00' WHERE id = 'teacher_001';
UPDATE user_account SET last_login_at = '2026-07-12 09:30:00' WHERE id = 'admin_001';

-- ============================================================
-- 九、对话会话(对应前端的 mockAIMessages)
-- ============================================================
INSERT IGNORE INTO conversation (session_id, student_id, title, status, message_count, tool_call_count) VALUES
('session_student_001_20260712', 'student_001', 'Vue 3 学习咨询', 'active', 4, 0),
('session_student_002_20260711', 'student_002', 'TypeScript 进阶', 'active', 2, 0);

INSERT IGNORE INTO conversation_message (session_id, message_id, role, content, iterations, elapsed_seconds) VALUES
('session_student_001_20260712', 'msg_001', 'user', '我想学习 Vue 3,有什么建议吗?', NULL, NULL),
('session_student_001_20260712', 'msg_002', 'assistant', '你好,张同学!关于 Vue 3 学习,我建议先掌握基础语法,然后深入 Composition API。', 2, 3.5),
('session_student_001_20260712', 'msg_003', 'user', 'Composition API 和 Options API 有什么区别?', NULL, NULL),
('session_student_001_20260712', 'msg_004', 'assistant', 'Composition API 允许按逻辑组织代码,而不是按选项类型。核心优势:1.更好的类型推导 2.逻辑复用更简洁 3.更灵活的代码组织。', 3, 5.2),
('session_student_002_20260711', 'msg_005', 'user', 'TypeScript 泛型怎么用?', NULL, NULL),
('session_student_002_20260711', 'msg_006', 'assistant', '泛型是 TypeScript 的核心特性,使用 <T> 定义类型参数。常见模式:函数泛型、接口泛型、类泛型。', 2, 4.0);

-- ============================================================
-- 十、收藏岗位
-- ============================================================
INSERT IGNORE INTO saved_job (user_id, job_id, job_title, company, industry, salary, location, match_score, required_skills) VALUES
('student_001', 'job_001', '前端开发工程师', '字节跳动', '互联网', '25-40K/月', '北京', 85.5, JSON_ARRAY('Vue 3', 'TypeScript', 'Webpack')),
('student_001', 'job_002', '高级前端开发', '腾讯', '互联网', '30-50K/月', '深圳', 78.2, JSON_ARRAY('React', 'Node.js', '性能优化')),
('student_002', 'job_003', 'TypeScript 全栈开发', '美团', '互联网', '20-35K/月', '北京', 88.0, JSON_ARRAY('TypeScript', 'Node.js', 'MySQL')),
('student_003', 'job_004', '初级前端开发', '小米', '智能硬件', '10-15K/月', '北京', 65.0, JSON_ARRAY('Vue', 'CSS', 'JavaScript'));

-- ============================================================
-- 十一、目标岗位
-- ============================================================
INSERT IGNORE INTO target_role (user_id, role_name, priority, salary_expectation, location_preference, notes) VALUES
('student_001', '前端开发工程师', 1, '25-40K/月', '北京/上海/深圳', '优先进大厂,积累 3 年经验后考虑管理路线'),
('student_001', '全栈工程师', 2, '30-45K/月', '北京', '作为备选方向,需要补 Node.js'),
('student_002', 'TypeScript 全栈开发', 1, '20-35K/月', '北京', '专注 TypeScript 技术栈'),
('student_003', '初级前端开发', 1, '10-18K/月', '北京/杭州', '先积累经验');

-- ============================================================
-- 十二、知识库记录(管理员上传)
-- ============================================================
INSERT IGNORE INTO knowledge_base (id, title, type, status, file_size, content_text, uploaded_by, created_at) VALUES
('kb_001', '前端开发岗位技能要求', 'skill', 'completed', 15680, '前端开发工程师需要掌握: HTML/CSS/JavaScript 基础, Vue/React 框架, 工程化工具...', 'admin_001', '2026-07-10 10:00:00'),
('kb_002', 'IT 行业岗位分类', 'jd', 'completed', 24800, 'IT 行业岗位分为: 前端、后端、测试、运维、产品、运营...', 'admin_001', '2026-07-09 14:00:00'),
('kb_003', '2026 校招行情分析', 'text', 'completed', 8400, '2026 届校招形势: AI 相关岗位需求增长 200%, 前端岗位竞争激烈...', 'admin_001', '2026-07-08 09:00:00'),
('kb_004', 'Vue 3 教程 PDF', 'file', 'processing', 5242880, NULL, 'admin_001', '2026-07-12 09:00:00'),
('kb_005', 'TypeScript 入门指南', 'text', 'pending', 3200, 'TypeScript 是 JavaScript 的超集...', 'admin_001', '2026-07-12 09:30:00');

-- ============================================================
-- 十三、数据集记录
-- ============================================================
INSERT IGNORE INTO job_dataset (id, title, count, category, status, file_size, description, uploaded_by, created_at) VALUES
('ds_001', '2026 Q2 互联网岗位数据', 5000, '互联网', 'completed', 8388608, '2026 年 4-6 月互联网行业岗位数据, 包含前端、后端、算法等岗位', 'admin_001', '2026-07-01 10:00:00'),
('ds_002', '金融科技岗位数据', 1200, '金融', 'completed', 2097152, '金融科技行业岗位数据, 涉及 Java、Python、前端等技术栈', 'admin_001', '2026-07-05 14:00:00'),
('ds_003', '2026 校招岗位汇总', 8500, '全行业', 'processing', 12582912, '2026 届校招全行业岗位汇总', 'admin_001', '2026-07-12 09:00:00'),
('ds_004', '人工智能岗位专项', 800, 'AI', 'pending', NULL, 'AI 算法工程师、NLP、CV 等岗位数据', 'admin_001', '2026-07-12 10:00:00');

-- ============================================================
-- 十四、OOV 实体(待审核)
-- ============================================================
INSERT IGNORE INTO oov_entity (oov_id, raw_entity, entity_type, context_json, source, frequency, status) VALUES
('oov_001', 'Next.js 13', 'skill', JSON_OBJECT('sentence', '熟悉 Next.js 13 App Router 开发', 'position', 'frontend_developer'), 'kb_001', 5, 'pending'),
('oov_002', 'Rust 后端开发', 'skill', JSON_OBJECT('sentence', '具有 Rust 后端开发经验', 'position', 'backend_engineer'), 'job_005', 3, 'pending'),
('oov_003', '云原生架构师', 'position', JSON_OBJECT('sentence', '招聘云原生架构师', 'industry', '云计算'), 'ds_001', 2, 'pending'),
('oov_004', '智能合约开发', 'skill', JSON_OBJECT('sentence', '智能合约开发经验', 'industry', '区块链'), 'ds_002', 1, 'pending'),
('oov_005', 'Solidity', 'skill', JSON_OBJECT('sentence', 'Solidity 编程经验', 'industry', '区块链'), 'ds_002', 1, 'pending');

-- 已审核的 OOV
INSERT IGNORE INTO oov_entity (oov_id, raw_entity, entity_type, status, canonical_mapping, reviewed_by, reviewed_at, review_notes) VALUES
('oov_006', 'React Hooks', 'skill', 'approved', 'React Hooks', 'admin_001', '2026-07-10 11:00:00', '已合并到 React 技能树'),
('oov_007', 'Tauri', 'skill', 'approved', 'Tauri', 'admin_001', '2026-07-10 11:30:00', '新型桌面应用框架'),
('oov_008', 'AIGC 应用开发', 'position', 'rejected', NULL, 'admin_001', '2026-07-11 09:00:00', '归类到 AI 应用开发工程师');

-- ============================================================
-- 十五、审计日志
-- ============================================================
INSERT IGNORE INTO admin_audit_log (admin_id, action, target_type, target_id, detail_json, ip, created_at) VALUES
('admin_001', 'login', 'user', 'admin_001', JSON_OBJECT('login_at', '2026-07-12 09:30:00'), '127.0.0.1', '2026-07-12 09:30:00'),
('admin_001', 'upload', 'knowledge_base', 'kb_001', JSON_OBJECT('title', '前端开发岗位技能要求', 'file_size', 15680), '127.0.0.1', '2026-07-10 10:00:00'),
('admin_001', 'upload', 'dataset', 'ds_001', JSON_OBJECT('title', '2026 Q2 互联网岗位数据', 'count', 5000), '127.0.0.1', '2026-07-01 10:00:00'),
('admin_001', 'approve', 'oov', 'oov_006', JSON_OBJECT('raw_entity', 'React Hooks', 'mapping', 'React Hooks'), '127.0.0.1', '2026-07-10 11:00:00'),
('admin_001', 'approve', 'oov', 'oov_007', JSON_OBJECT('raw_entity', 'Tauri', 'mapping', 'Tauri'), '127.0.0.1', '2026-07-10 11:30:00'),
('admin_001', 'reject', 'oov', 'oov_008', JSON_OBJECT('raw_entity', 'AIGC 应用开发', 'reason', '归类到 AI 应用开发工程师'), '127.0.0.1', '2026-07-11 09:00:00'),
('admin_001', 'create', 'config', 'cfg_001', JSON_OBJECT('setting', 'OOV 阈值调整为 3'), '127.0.0.1', '2026-07-09 15:00:00');

-- ============================================================
-- 十六、规范化实体(给 OOV 审核通过的数据)
-- ============================================================
INSERT IGNORE INTO canonical_entity (entity_type, canonical_name, category, aliases_json, frequency, source) VALUES
('skill', 'React Hooks', '前端框架', JSON_ARRAY('Hooks', 'React Hooks API'), 1, 'oov_review'),
('skill', 'Tauri', '桌面应用', JSON_ARRAY('Tauri Framework'), 1, 'oov_review'),
('skill', 'Vue 3', '前端框架', JSON_ARRAY('Vue.js 3', 'Vue3'), 50, 'seed'),
('skill', 'TypeScript', '前端语言', JSON_ARRAY('TS'), 45, 'seed'),
('skill', 'Node.js', '后端语言', JSON_ARRAY('NodeJS', 'Node'), 30, 'seed'),
('skill', 'MySQL', '数据库', JSON_ARRAY('My SQL'), 25, 'seed'),
('skill', 'Python', 'AI 语言', JSON_ARRAY('python'), 40, 'seed'),
('position', '前端开发工程师', '前端', JSON_ARRAY('Web 前端', 'Frontend Engineer'), 100, 'seed'),
('position', '后端开发工程师', '后端', JSON_ARRAY('Backend Engineer'), 80, 'seed'),
('position', 'AI 算法工程师', 'AI', JSON_ARRAY('算法工程师'), 60, 'seed'),
('degree', '本科', NULL, JSON_ARRAY('本科及以上', 'Bachelor'), 200, 'seed'),
('degree', '硕士', NULL, JSON_ARRAY('硕士研究生', 'Master'), 80, 'seed');

-- ============================================================
-- 十七、管线任务
-- ============================================================
INSERT IGNORE INTO pipeline_task (task_id, pipeline, status, progress, student_id, job_id, triggered_by, started_at, completed_at, created_at) VALUES
('task_l0_001', 'L0_parse_resume', 'completed', 100, 'student_001', NULL, 'api', '2026-07-10 09:00:00', '2026-07-10 09:00:25', '2026-07-10 09:00:00'),
('task_l2_001', 'L2_generate_portrait', 'completed', 100, 'student_001', 'job_001', 'api', '2026-07-10 09:01:00', '2026-07-10 09:01:42', '2026-07-10 09:01:00'),
('task_l3_001', 'L3_run_match', 'completed', 100, 'student_001', 'job_001', 'api', '2026-07-10 09:02:00', '2026-07-10 09:02:15', '2026-07-10 09:02:00'),
('task_l4_001', 'L4_generate_report', 'completed', 100, 'student_001', 'job_001', 'api', '2026-07-10 09:03:00', '2026-07-10 09:03:38', '2026-07-10 09:03:00'),
('task_l0_002', 'L0_parse_resume', 'completed', 100, 'student_002', NULL, 'api', '2026-07-10 10:00:00', '2026-07-10 10:00:22', '2026-07-10 10:00:00'),
('task_l0_003', 'L0_parse_resume', 'completed', 100, 'student_003', NULL, 'api', '2026-07-10 11:00:00', '2026-07-10 11:00:18', '2026-07-10 11:00:00'),
('task_l0_004', 'L0_parse_resume', 'running', 65, 'student_004', NULL, 'agent', '2026-07-12 09:45:00', NULL, '2026-07-12 09:45:00'),
('task_l2_002', 'L2_generate_portrait', 'failed', 0, 'student_005', 'job_002', 'api', '2026-07-12 10:00:00', '2026-07-12 10:00:05', '2026-07-12 10:00:00'),
('task_l4_002', 'L4_generate_report', 'pending', 0, 'student_001', 'job_002', 'api', NULL, NULL, '2026-07-12 10:30:00');

-- 更新 L4 的失败任务错误信息
UPDATE pipeline_task SET error_message = 'LLM API 调用超时' WHERE task_id = 'task_l2_002';
UPDATE pipeline_task SET error_message = NULL WHERE task_id != 'task_l2_002';

-- ============================================================
-- 十八、补充:为 5 个学生插入简历 + 画像(用于 v_student_full_profile)
-- ============================================================
INSERT IGNORE INTO student_profile (student_id, name, phone, email, degree, school, major, graduation_year, profile_json, completeness) VALUES
('student_001', '张同学', '13800138001', 'student001@example.com', '本科', '清华大学', '计算机科学与技术', 2025, JSON_OBJECT('name', '张同学', 'school', '清华大学'), 0.95),
('student_002', '李同学', NULL, 'student002@example.com', '本科', '北京大学', '软件工程', 2025, JSON_OBJECT('name', '李同学', 'school', '北京大学'), 0.90),
('student_003', '王同学', NULL, 'student003@example.com', '本科', '复旦大学', '信息工程', 2025, JSON_OBJECT('name', '王同学', 'school', '复旦大学'), 0.85);

-- 学生画像(D1-D4)
INSERT IGNORE INTO student_portrait_4d (student_id, target_job_id, d1_score, d2_score, d3_score, d4_score, competitiveness, competitiveness_lvl, completeness, skill_coverage, skill_count, d3_confidence, portrait_json) VALUES
('student_001', 'job_001', 85.0, 80.0, 78.0, 82.0, 81.3, '强', 95.0, 0.80, 25, 0.75, JSON_OBJECT('student_id', 'student_001', 'd1', 85)),
('student_002', 'job_003', 88.0, 85.0, 75.0, 86.0, 84.0, '强', 90.0, 0.85, 28, 0.70, JSON_OBJECT('student_id', 'student_002', 'd1', 88)),
('student_003', 'job_004', 80.0, 65.0, 72.0, 70.0, 71.3, '中等', 85.0, 0.65, 18, 0.65, JSON_OBJECT('student_id', 'student_003', 'd1', 80));

-- 技能差距
INSERT IGNORE INTO skill_gap (student_id, job_portrait_id, critical_count, quick_learn_count, total_gap_count, gap_json) VALUES
('student_001', 'job_001', 2, 3, 5, JSON_OBJECT('student_id', 'student_001', 'critical_skills', JSON_ARRAY('React', 'Node.js'))),
('student_002', 'job_003', 1, 2, 3, JSON_OBJECT('student_id', 'student_002', 'critical_skills', JSON_ARRAY('Docker'))),
('student_003', 'job_004', 4, 5, 9, JSON_OBJECT('student_id', 'student_003', 'critical_skills', JSON_ARRAY('TypeScript', 'Vue 3', 'Webpack', '性能优化')));

-- 职业路径
INSERT IGNORE INTO career_path (student_id, job_portrait_id, recommended_dir, promotion_steps, lateral_count, path_json) VALUES
('student_001', 'job_001', '前端开发工程师 → 高级前端 → 前端架构师', 3, 2, JSON_OBJECT('student_id', 'student_001', 'path', 'senior_track')),
('student_002', 'job_003', '全栈工程师 → 技术负责人', 2, 1, JSON_OBJECT('student_id', 'student_002', 'path', 'fullstack_track'));

-- 职业报告
INSERT IGNORE INTO career_report (report_id, student_id, job_portrait_id, title, status, ch1_json, ch2_json, ch3_json, ch4_json, ch5_json, ch6_json, report_json, elapsed_seconds) VALUES
('report_001', 'student_001', 'job_001', '张同学-前端开发工程师职业发展报告', 'completed',
    JSON_OBJECT('summary', '张同学,清华大学计算机科学专业,具备扎实的前端基础和丰富的项目经验'),
    JSON_OBJECT('target', '前端开发工程师', 'jd_count', 150),
    JSON_OBJECT('total_score', 81.3, 'level', '强'),
    JSON_OBJECT('path', '前端开发工程师 → 高级前端 → 前端架构师'),
    JSON_OBJECT('gaps', '需要补齐 React、Node.js 等技能'),
    JSON_OBJECT('cycle', '3 个月短期, 1 年中期, 3 年长期'),
    JSON_OBJECT('student_id', 'student_001'), 38.5),
('report_002', 'student_002', 'job_003', '李同学-TypeScript 全栈开发职业发展报告', 'generating',
    JSON_OBJECT('summary', '李同学,北京大学软件工程专业'),
    JSON_OBJECT('target', 'TypeScript 全栈开发', 'jd_count', 80),
    NULL, NULL, NULL, NULL,
    JSON_OBJECT('student_id', 'student_002'), NULL);

-- ============================================================
-- 验证数据
-- ============================================================
SELECT '========= 班级 =========' AS section, COUNT(*) AS cnt FROM class_info
UNION ALL
SELECT '========= 用户账号 =========', COUNT(*) FROM user_account
UNION ALL
SELECT '========= 学生(角色) =========', COUNT(*) FROM user_account WHERE role='student'
UNION ALL
SELECT '========= 教师(角色) =========', COUNT(*) FROM user_account WHERE role='teacher'
UNION ALL
SELECT '========= 管理员(角色) =========', COUNT(*) FROM user_account WHERE role='admin'
UNION ALL
SELECT '========= 学生简历 =========', COUNT(*) FROM student_profile
UNION ALL
SELECT '========= 课程 =========', COUNT(*) FROM course
UNION ALL
SELECT '========= 章节 =========', COUNT(*) FROM chapter
UNION ALL
SELECT '========= 学习进度 =========', COUNT(*) FROM learning_progress
UNION ALL
SELECT '========= 学生成绩 =========', COUNT(*) FROM student_grade
UNION ALL
SELECT '========= 登录日志 =========', COUNT(*) FROM active_user_log
UNION ALL
SELECT '========= 对话会话 =========', COUNT(*) FROM conversation
UNION ALL
SELECT '========= 对话消息 =========', COUNT(*) FROM conversation_message
UNION ALL
SELECT '========= 收藏岗位 =========', COUNT(*) FROM saved_job
UNION ALL
SELECT '========= 目标岗位 =========', COUNT(*) FROM target_role
UNION ALL
SELECT '========= 知识库 =========', COUNT(*) FROM knowledge_base
UNION ALL
SELECT '========= 数据集 =========', COUNT(*) FROM job_dataset
UNION ALL
SELECT '========= OOV 实体 =========', COUNT(*) FROM oov_entity
UNION ALL
SELECT '========= 审计日志 =========', COUNT(*) FROM admin_audit_log
UNION ALL
SELECT '========= 规范实体 =========', COUNT(*) FROM canonical_entity
UNION ALL
SELECT '========= 管线任务 =========', COUNT(*) FROM pipeline_task
UNION ALL
SELECT '========= 技能差距 =========', COUNT(*) FROM skill_gap
UNION ALL
SELECT '========= 职业路径 =========', COUNT(*) FROM career_path
UNION ALL
SELECT '========= 职业报告 =========', COUNT(*) FROM career_report;
