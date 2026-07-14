-- ============================================================
-- CPA-Agent 视图设计
-- 版本: v1.0
-- 日期: 2026-07-12
-- 说明: 20 个业务视图, 覆盖学生画像/人岗匹配/管理员监控/学习进度等场景
-- ============================================================

-- ------------------------------------------------------------
-- 阶段一: 先创建 5 张 API 扩展表(视图依赖)
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS saved_job (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL COMMENT '用户ID',
    job_id          VARCHAR(64)  NOT NULL COMMENT '岗位ID',
    job_title       VARCHAR(200) DEFAULT NULL COMMENT '岗位名称',
    company         VARCHAR(200) DEFAULT NULL COMMENT '公司名称',
    industry        VARCHAR(100) DEFAULT NULL COMMENT '行业',
    salary          VARCHAR(100) DEFAULT NULL COMMENT '薪资范围',
    location        VARCHAR(200) DEFAULT NULL COMMENT '工作地点',
    match_score     FLOAT        DEFAULT NULL COMMENT '匹配分数',
    required_skills JSON         DEFAULT NULL COMMENT '必备技能列表',
    saved_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_job (job_id),
    INDEX idx_saved (saved_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='收藏岗位';

CREATE TABLE IF NOT EXISTS target_role (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL COMMENT '用户ID',
    role_name       VARCHAR(200) NOT NULL COMMENT '目标岗位名称',
    priority        INT          NOT NULL DEFAULT 1 COMMENT '优先级',
    salary_expectation VARCHAR(100) DEFAULT NULL COMMENT '薪资期望',
    location_preference VARCHAR(200) DEFAULT NULL COMMENT '地点偏好',
    notes           TEXT         DEFAULT NULL COMMENT '备注',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_priority (priority DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='目标岗位';

CREATE TABLE IF NOT EXISTS course (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    course_id       VARCHAR(64)  NOT NULL UNIQUE COMMENT '课程唯一ID',
    title           VARCHAR(200) NOT NULL COMMENT '课程标题',
    category        VARCHAR(100) DEFAULT NULL COMMENT '课程分类',
    description     TEXT         DEFAULT NULL COMMENT '课程描述',
    difficulty      VARCHAR(20)  DEFAULT 'beginner' COMMENT '难度: beginner/intermediate/advanced',
    duration_hours  FLOAT        DEFAULT 0 COMMENT '课程时长(小时)',
    instructor      VARCHAR(100) DEFAULT NULL COMMENT '讲师',
    thumbnail_url   VARCHAR(500) DEFAULT NULL COMMENT '缩略图URL',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_category (category),
    INDEX idx_difficulty (difficulty)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='课程';

CREATE TABLE IF NOT EXISTS chapter (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    course_id       VARCHAR(64)  NOT NULL COMMENT '课程ID',
    title           VARCHAR(200) NOT NULL COMMENT '章节标题',
    sequence        INT          NOT NULL DEFAULT 0 COMMENT '章节序号',
    duration_hours  FLOAT        DEFAULT 0 COMMENT '章节时长(小时)',
    content_summary TEXT         DEFAULT NULL COMMENT '内容摘要',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_course (course_id),
    INDEX idx_sequence (sequence)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='课程章节';

CREATE TABLE IF NOT EXISTS learning_progress (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL COMMENT '用户ID',
    course_id       VARCHAR(64)  NOT NULL COMMENT '课程ID',
    chapter_id      VARCHAR(64)  NOT NULL COMMENT '章节ID',
    progress        FLOAT        NOT NULL DEFAULT 0 COMMENT '进度 0-100',
    completed       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否完成',
    last_position   INT          DEFAULT 0 COMMENT '上次播放位置(秒)',
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    completed_at    DATETIME     DEFAULT NULL COMMENT '完成时间',
    UNIQUE KEY uk_user_chapter (user_id, chapter_id),
    INDEX idx_user_course (user_id, course_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学习进度';

-- ------------------------------------------------------------
-- 阶段二: 创建 20 个业务视图
-- ------------------------------------------------------------

-- ============================================================
-- 一、学生业务视图(6 个)
-- ============================================================

-- 1. v_student_full_profile - 学生完整画像
-- 用途: 学生主页展示, 聚合账号+简历+四维画像+综合竞争力
-- 依赖: user_account, student_profile, student_portrait_4d
CREATE OR REPLACE VIEW v_student_full_profile AS
SELECT
    u.id AS user_id,
    u.username,
    u.name,
    u.email,
    u.phone,
    u.avatar,
    u.class_id,
    u.role,
    sp.student_id,
    sp.degree,
    sp.school,
    sp.major,
    sp.graduation_year,
    sp.completeness AS profile_completeness,
    sp.source_type,
    p4.d1_score,
    p4.d2_score,
    p4.d3_score,
    p4.d4_score,
    p4.competitiveness,
    p4.competitiveness_lvl,
    p4.skill_coverage,
    p4.skill_count,
    p4.target_job_id,
    u.created_at,
    u.updated_at
FROM user_account u
LEFT JOIN student_profile sp ON u.id = sp.student_id
LEFT JOIN student_portrait_4d p4 ON sp.student_id = p4.student_id
WHERE u.role = 'student';

-- 2. v_job_match_summary - 人岗匹配汇总
-- 用途: 学生查看与各岗位的匹配情况
-- 依赖: student_profile, job_portrait_4d, match_result
CREATE OR REPLACE VIEW v_job_match_summary AS
SELECT
    mr.id AS match_id,
    sp.student_id,
    sp.name AS student_name,
    sp.school,
    sp.major,
    jp.portrait_id,
    jp.position_name AS job_position,
    jp.jd_count,
    jp.required_degree,
    mr.d1_match,
    mr.d2_match,
    mr.d3_match,
    mr.d4_match,
    mr.total_score,
    mr.d1_pass,
    mr.skill_coverage,
    mr.is_matched,
    mr.match_level,
    mr.created_at AS matched_at
FROM match_result mr
JOIN student_profile sp ON mr.student_id = sp.student_id
JOIN job_portrait_4d jp ON mr.job_portrait_id = jp.portrait_id;

-- 3. v_student_skill_gap_detail - 学生技能差距详情
-- 用途: 学生查看与目标岗位的技能差距
-- 依赖: student_profile, job_portrait_4d, skill_gap
CREATE OR REPLACE VIEW v_student_skill_gap_detail AS
SELECT
    sg.id AS gap_id,
    sp.student_id,
    sp.name AS student_name,
    jp.position_name AS target_position,
    sg.critical_count,
    sg.quick_learn_count,
    sg.total_gap_count,
    CASE
        WHEN sg.critical_count = 0 AND sg.total_gap_count = 0 THEN '无差距'
        WHEN sg.critical_count = 0 THEN '轻微差距'
        WHEN sg.critical_count <= 2 THEN '中等差距'
        ELSE '严重差距'
    END AS gap_level,
    sg.created_at
FROM skill_gap sg
JOIN student_profile sp ON sg.student_id = sp.student_id
JOIN job_portrait_4d jp ON sg.job_portrait_id = jp.portrait_id;

-- 4. v_career_report_summary - 职业报告摘要
-- 用途: 学生查看已生成的职业报告列表
-- 依赖: student_profile, job_portrait_4d, career_report
CREATE OR REPLACE VIEW v_career_report_summary AS
SELECT
    cr.report_id,
    sp.student_id,
    sp.name AS student_name,
    jp.position_name AS target_position,
    cr.title,
    cr.status,
    CASE
        WHEN cr.status = 'completed' THEN '已完成'
        WHEN cr.status = 'generating' THEN '生成中'
        WHEN cr.status = 'pending' THEN '等待中'
        WHEN cr.status = 'failed' THEN '失败'
        ELSE cr.status
    END AS status_label,
    CASE
        WHEN cr.ch1_json IS NOT NULL AND cr.ch6_json IS NOT NULL THEN 6
        WHEN cr.ch5_json IS NOT NULL THEN 5
        WHEN cr.ch4_json IS NOT NULL THEN 4
        WHEN cr.ch3_json IS NOT NULL THEN 3
        WHEN cr.ch2_json IS NOT NULL THEN 2
        WHEN cr.ch1_json IS NOT NULL THEN 1
        ELSE 0
    END AS completed_chapters,
    cr.elapsed_seconds,
    cr.html_path,
    cr.pdf_path,
    cr.created_at
FROM career_report cr
JOIN student_profile sp ON cr.student_id = sp.student_id
JOIN job_portrait_4d jp ON cr.job_portrait_id = jp.portrait_id;

-- 5. v_career_path_detail - 职业发展路径详情
-- 用途: 学生查看推荐的发展路径
-- 依赖: student_profile, job_portrait_4d, career_path
CREATE OR REPLACE VIEW v_career_path_detail AS
SELECT
    cp.id AS path_id,
    sp.student_id,
    sp.name AS student_name,
    jp.position_name AS target_position,
    cp.recommended_dir,
    cp.promotion_steps,
    cp.lateral_count,
    CASE
        WHEN cp.promotion_steps >= 5 THEN '清晰长路径'
        WHEN cp.promotion_steps >= 3 THEN '标准路径'
        WHEN cp.promotion_steps >= 1 THEN '短路径'
        ELSE '无明确路径'
    END AS path_type,
    cp.created_at
FROM career_path cp
JOIN student_profile sp ON cp.student_id = sp.student_id
JOIN job_portrait_4d jp ON cp.job_portrait_id = jp.portrait_id;

-- 6. v_student_career_full - 学生职业全貌
-- 用途: 一站式查看学生所有职业规划数据(画像+匹配+差距+路径+报告)
-- 依赖: student_profile, job_portrait_4d, student_portrait_4d, match_result, skill_gap, career_path, career_report
CREATE OR REPLACE VIEW v_student_career_full AS
SELECT
    sp.student_id,
    sp.name AS student_name,
    sp.school,
    sp.major,
    jp.position_name AS target_position,
    p4.competitiveness,
    p4.competitiveness_lvl,
    mr.total_score AS match_score,
    mr.match_level,
    mr.is_matched,
    sg.total_gap_count,
    sg.critical_count,
    cp.recommended_dir,
    cp.promotion_steps,
    cr.report_id,
    cr.status AS report_status
FROM student_profile sp
LEFT JOIN student_portrait_4d p4 ON sp.student_id = p4.student_id
LEFT JOIN job_portrait_4d jp ON p4.target_job_id = jp.portrait_id
LEFT JOIN match_result mr ON sp.student_id = mr.student_id
LEFT JOIN skill_gap sg ON sp.student_id = sg.student_id
LEFT JOIN career_path cp ON sp.student_id = cp.student_id
LEFT JOIN career_report cr ON sp.student_id = cr.student_id;

-- ============================================================
-- 二、账号与权限视图(4 个)
-- ============================================================

-- 7. v_student_class_info - 学生班级信息
-- 用途: 学生个人页展示班级信息
-- 依赖: user_account, class_info, student_profile
CREATE OR REPLACE VIEW v_student_class_info AS
SELECT
    u.id AS user_id,
    u.username,
    u.name,
    u.email,
    u.phone,
    u.avatar,
    u.class_id,
    ci.name AS class_name,
    ci.description AS class_desc,
    ci.student_count,
    teacher.name AS teacher_name,
    teacher.email AS teacher_email,
    sp.student_id,
    sp.degree,
    sp.school,
    sp.major,
    sp.graduation_year
FROM user_account u
LEFT JOIN class_info ci ON u.class_id = ci.id
LEFT JOIN user_account teacher ON ci.teacher_id = teacher.id
LEFT JOIN student_profile sp ON u.id = sp.student_id
WHERE u.role = 'student';

-- 8. v_teacher_students - 教师名下学生列表
-- 用途: 教师查看自己班级的所有学生
-- 依赖: user_account, class_info, student_profile
CREATE OR REPLACE VIEW v_teacher_students AS
SELECT
    teacher.id AS teacher_id,
    teacher.name AS teacher_name,
    ci.id AS class_id,
    ci.name AS class_name,
    u.id AS student_user_id,
    u.username AS student_username,
    u.name AS student_name,
    u.email AS student_email,
    u.phone AS student_phone,
    u.status AS student_status,
    sp.student_id,
    sp.degree,
    sp.school,
    sp.major,
    sp.graduation_year,
    sp.completeness AS profile_completeness,
    u.last_login_at,
    u.created_at
FROM user_account teacher
JOIN class_info ci ON teacher.id = ci.teacher_id
JOIN user_account u ON u.class_id = ci.id AND u.role = 'student'
LEFT JOIN student_profile sp ON u.id = sp.student_id
WHERE teacher.role = 'teacher';

-- 9. v_user_activity_stats - 用户活跃统计
-- 用途: DAU/WAU/MAU 统计数据源, 管理员仪表盘
-- 依赖: user_account, active_user_log
CREATE OR REPLACE VIEW v_user_activity_stats AS
SELECT
    u.id AS user_id,
    u.username,
    u.name,
    u.role,
    u.status,
    u.last_login_at,
    u.last_login_ip,
    COUNT(aul.id) AS total_logins,
    SUM(CASE WHEN DATE(aul.login_at) = CURDATE() THEN 1 ELSE 0 END) AS dau,
    SUM(CASE WHEN aul.login_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) THEN 1 ELSE 0 END) AS wau,
    SUM(CASE WHEN aul.login_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) THEN 1 ELSE 0 END) AS mau,
    MAX(aul.login_at) AS latest_activity
FROM user_account u
LEFT JOIN active_user_log aul ON u.id = aul.user_id
GROUP BY u.id, u.username, u.name, u.role, u.status, u.last_login_at, u.last_login_ip;

-- 10. v_admin_operations - 管理员操作记录
-- 用途: 管理员审计日志展示, 关联管理员信息
-- 依赖: admin_audit_log, user_account
CREATE OR REPLACE VIEW v_admin_operations AS
SELECT
    aal.id AS log_id,
    aal.admin_id,
    admin.username AS admin_username,
    admin.name AS admin_name,
    aal.action,
    aal.target_type,
    aal.target_id,
    aal.detail_json,
    aal.ip,
    aal.created_at,
    CASE
        WHEN aal.action = 'create' THEN '创建'
        WHEN aal.action = 'update' THEN '更新'
        WHEN aal.action = 'delete' THEN '删除'
        WHEN aal.action = 'upload' THEN '上传'
        WHEN aal.action = 'approve' THEN '通过'
        WHEN aal.action = 'reject' THEN '拒绝'
        WHEN aal.action = 'login' THEN '登录'
        ELSE aal.action
    END AS action_label,
    CASE
        WHEN aal.target_type = 'knowledge_base' THEN '知识库'
        WHEN aal.target_type = 'dataset' THEN '数据集'
        WHEN aal.target_type = 'oov' THEN 'OOV实体'
        WHEN aal.target_type = 'user' THEN '用户'
        WHEN aal.target_type = 'config' THEN '配置'
        ELSE aal.target_type
    END AS target_type_label
FROM admin_audit_log aal
JOIN user_account admin ON aal.admin_id = admin.id
ORDER BY aal.created_at DESC;

-- ============================================================
-- 三、管理员业务视图(4 个)
-- ============================================================

-- 11. v_knowledge_base_status - 知识库处理状态
-- 用途: 管理员监控知识库上传和处理进度
-- 依赖: knowledge_base, user_account
CREATE OR REPLACE VIEW v_knowledge_base_status AS
SELECT
    kb.id AS kb_id,
    kb.title,
    kb.type,
    CASE
        WHEN kb.type = 'text' THEN '文本'
        WHEN kb.type = 'skill' THEN '技能'
        WHEN kb.type = 'jd' THEN '岗位描述'
        WHEN kb.type = 'file' THEN '文件'
        ELSE kb.type
    END AS type_label,
    kb.status,
    CASE
        WHEN kb.status = 'pending' THEN '等待中'
        WHEN kb.status = 'processing' THEN '处理中'
        WHEN kb.status = 'completed' THEN '已完成'
        WHEN kb.status = 'failed' THEN '失败'
        ELSE kb.status
    END AS status_label,
    kb.file_size,
    CASE WHEN kb.parsed_entities IS NOT NULL THEN 1 ELSE 0 END AS is_parsed,
    CASE WHEN kb.intent_json IS NOT NULL THEN 1 ELSE 0 END AS has_intent,
    kb.uploaded_by,
    uploader.name AS uploader_name,
    kb.error_message,
    kb.created_at,
    kb.updated_at,
    TIMESTAMPDIFF(MINUTE, kb.created_at, kb.updated_at) AS processing_minutes
FROM knowledge_base kb
LEFT JOIN user_account uploader ON kb.uploaded_by = uploader.id;

-- 12. v_dataset_status - 岗位数据集状态
-- 用途: 管理员监控岗位数据集上传和处理
-- 依赖: job_dataset, user_account
CREATE OR REPLACE VIEW v_dataset_status AS
SELECT
    ds.id AS dataset_id,
    ds.title,
    ds.count AS job_count,
    ds.category,
    ds.status,
    CASE
        WHEN ds.status = 'pending' THEN '等待中'
        WHEN ds.status = 'processing' THEN '处理中'
        WHEN ds.status = 'completed' THEN '已完成'
        WHEN ds.status = 'failed' THEN '失败'
        ELSE ds.status
    END AS status_label,
    ds.file_size,
    ds.uploaded_by,
    uploader.name AS uploader_name,
    ds.error_message,
    ds.created_at,
    ds.updated_at,
    TIMESTAMPDIFF(MINUTE, ds.created_at, ds.updated_at) AS processing_minutes
FROM job_dataset ds
LEFT JOIN user_account uploader ON ds.uploaded_by = uploader.id;

-- 13. v_oov_pending_review - OOV 待审核队列
-- 用途: 管理员查看待审核的 OOV 实体
-- 依赖: oov_entity
CREATE OR REPLACE VIEW v_oov_pending_review AS
SELECT
    oov.oov_id,
    oov.raw_entity,
    oov.entity_type,
    CASE
        WHEN oov.entity_type = 'skill' THEN '技能'
        WHEN oov.entity_type = 'position' THEN '岗位'
        WHEN oov.entity_type = 'major' THEN '专业'
        WHEN oov.entity_type = 'certificate' THEN '证书'
        WHEN oov.entity_type = 'degree' THEN '学历'
        WHEN oov.entity_type = 'award' THEN '奖项'
        ELSE oov.entity_type
    END AS entity_type_label,
    oov.context_json,
    oov.source,
    oov.frequency,
    oov.created_at,
    TIMESTAMPDIFF(HOUR, oov.created_at, NOW()) AS pending_hours
FROM oov_entity oov
WHERE oov.status = 'pending'
ORDER BY oov.frequency DESC, oov.created_at ASC;

-- 14. v_oov_reviewed_history - OOV 审核历史
-- 用途: 管理员查看已审核的 OOV 实体历史
-- 依赖: oov_entity, user_account, canonical_entity
CREATE OR REPLACE VIEW v_oov_reviewed_history AS
SELECT
    oov.oov_id,
    oov.raw_entity,
    oov.entity_type,
    oov.status,
    CASE
        WHEN oov.status = 'approved' THEN '已通过'
        WHEN oov.status = 'rejected' THEN '已拒绝'
        ELSE oov.status
    END AS status_label,
    oov.canonical_mapping,
    ce.category AS canonical_category,
    oov.reviewed_by,
    reviewer.name AS reviewer_name,
    oov.reviewed_at,
    oov.review_notes,
    oov.frequency,
    oov.created_at
FROM oov_entity oov
LEFT JOIN user_account reviewer ON oov.reviewed_by = reviewer.id
LEFT JOIN canonical_entity ce ON oov.canonical_mapping = ce.canonical_name
    AND oov.entity_type = ce.entity_type
WHERE oov.status IN ('approved', 'rejected')
ORDER BY oov.reviewed_at DESC;

-- ============================================================
-- 四、冷启动与学习视图(4 个)
-- ============================================================

-- 15. v_career_skill_map - 岗位技能图谱
-- 用途: 岗位与技能的关联视图, 用于前端能力图谱展示
-- 依赖: career_insights, skill_taxonomy
CREATE OR REPLACE VIEW v_career_skill_map AS
SELECT
    ci.role_name,
    ci.category AS role_category,
    ci.sub_category,
    ci.avg_salary_low,
    ci.avg_salary_high,
    ci.demand_level,
    ci.entry_difficulty,
    ci.growth_trend,
    ci.description AS role_description,
    ci.skill_count AS role_skill_count,
    ci.core_skills,
    st.skill_name,
    st.category AS skill_category,
    st.tier AS skill_tier,
    st.difficulty AS skill_difficulty,
    st.weeks_to_learn,
    st.heat_score AS skill_heat,
    st.dimension AS skill_dimension,
    CASE
        WHEN st.tier = 1 THEN '基础'
        WHEN st.tier = 2 THEN '进阶'
        WHEN st.tier = 3 THEN '高级'
        ELSE '未分级'
    END AS tier_label
FROM career_insights ci
LEFT JOIN skill_taxonomy st ON JSON_CONTAINS(ci.core_skills, JSON_QUOTE(st.skill_name))
WHERE ci.role_name IS NOT NULL;

-- 16. v_course_progress_detail - 课程学习进度详情
-- 用途: 学生查看自己所有课程的章节进度
-- 依赖: user_account, course, chapter, learning_progress
CREATE OR REPLACE VIEW v_course_progress_detail AS
SELECT
    lp.user_id,
    u.name AS user_name,
    lp.course_id,
    c.title AS course_title,
    c.category AS course_category,
    c.difficulty AS course_difficulty,
    c.instructor,
    c.duration_hours AS course_duration,
    lp.chapter_id,
    ch.title AS chapter_title,
    ch.sequence AS chapter_sequence,
    ch.duration_hours AS chapter_duration,
    lp.progress,
    lp.completed,
    lp.last_position,
    lp.updated_at AS progress_updated,
    lp.completed_at
FROM learning_progress lp
JOIN user_account u ON lp.user_id = u.id
JOIN course c ON lp.course_id = c.course_id
LEFT JOIN chapter ch ON lp.chapter_id = ch.id;

-- 17. v_student_grade_summary - 学生成绩汇总
-- 用途: 教师/管理员查看班级学生成绩汇总
-- 依赖: user_account, class_info, course, student_grade
CREATE OR REPLACE VIEW v_student_grade_summary AS
SELECT
    sg.user_id,
    u.username,
    u.name AS student_name,
    u.class_id,
    ci.name AS class_name,
    sg.course_id,
    c.title AS course_title,
    c.difficulty AS course_difficulty,
    sg.total_score,
    sg.quiz_scores,
    sg.completion_rate,
    CASE
        WHEN sg.total_score >= 90 THEN '优秀'
        WHEN sg.total_score >= 80 THEN '良好'
        WHEN sg.total_score >= 70 THEN '中等'
        WHEN sg.total_score >= 60 THEN '及格'
        ELSE '不及格'
    END AS grade_level,
    CASE
        WHEN sg.completion_rate >= 0.9 THEN '完成'
        WHEN sg.completion_rate >= 0.5 THEN '进行中'
        ELSE '未开始'
    END AS completion_status,
    sg.graded_at,
    sg.updated_at
FROM student_grade sg
JOIN user_account u ON sg.user_id = u.id
LEFT JOIN class_info ci ON u.class_id = ci.id
JOIN course c ON sg.course_id = c.course_id;

-- 18. v_learning_path_full - 学习路径详情
-- 用途: 展示完整的学习路径(模板+关联技能)
-- 依赖: learning_path_template, skill_taxonomy
CREATE OR REPLACE VIEW v_learning_path_full AS
SELECT
    lpt.path_id,
    lpt.target_role,
    lpt.target_level,
    lpt.steps,
    lpt.total_weeks,
    lpt.description,
    st.skill_name,
    st.category AS skill_category,
    st.tier,
    st.difficulty,
    st.weeks_to_learn,
    st.heat_score,
    st.dimension,
    st.description AS skill_description,
    CASE
        WHEN st.tier = 1 THEN '基础'
        WHEN st.tier = 2 THEN '进阶'
        WHEN st.tier = 3 THEN '高级'
        ELSE '未分级'
    END AS tier_label
FROM learning_path_template lpt
LEFT JOIN skill_taxonomy st ON JSON_CONTAINS(lpt.steps, JSON_QUOTE(st.skill_name), '$[*].skill')
ORDER BY lpt.target_role, st.tier;

-- ============================================================
-- 五、会话与任务视图(2 个)
-- ============================================================

-- 19. v_conversation_full - 对话完整记录
-- 用途: 学生/管理员查看对话历史(含消息统计)
-- 依赖: conversation, conversation_message, student_profile
CREATE OR REPLACE VIEW v_conversation_full AS
SELECT
    c.id,
    c.session_id,
    c.student_id,
    sp.name AS student_name,
    c.title,
    c.status,
    CASE
        WHEN c.status = 'active' THEN '活跃'
        WHEN c.status = 'archived' THEN '已归档'
        WHEN c.status = 'deleted' THEN '已删除'
        ELSE c.status
    END AS status_label,
    c.message_count,
    c.tool_call_count,
    COUNT(cm.id) AS actual_message_count,
    MAX(cm.created_at) AS last_message_at,
    SUM(CASE WHEN cm.role = 'user' THEN 1 ELSE 0 END) AS user_messages,
    SUM(CASE WHEN cm.role = 'assistant' THEN 1 ELSE 0 END) AS assistant_messages,
    SUM(CASE WHEN cm.role = 'system' THEN 1 ELSE 0 END) AS system_messages,
    AVG(cm.iterations) AS avg_iterations,
    AVG(cm.elapsed_seconds) AS avg_response_seconds,
    c.created_at,
    c.updated_at
FROM conversation c
LEFT JOIN conversation_message cm ON c.session_id = cm.session_id
LEFT JOIN student_profile sp ON c.student_id = sp.student_id
GROUP BY c.id, c.session_id, c.student_id, sp.name, c.title, c.status,
         c.message_count, c.tool_call_count, c.created_at, c.updated_at;

-- 20. v_pipeline_monitor - 管线任务监控
-- 用途: 管理员监控 L0-L4 管线任务执行情况
-- 依赖: pipeline_task, student_profile
CREATE OR REPLACE VIEW v_pipeline_monitor AS
SELECT
    pt.task_id,
    pt.pipeline,
    CASE
        WHEN pt.pipeline = 'L0_parse_resume' THEN 'L0 简历解析'
        WHEN pt.pipeline = 'L1_build_graph' THEN 'L1 知识图谱构建'
        WHEN pt.pipeline = 'L2_generate_portrait' THEN 'L2 四维画像生成'
        WHEN pt.pipeline = 'L3_run_match' THEN 'L3 人岗匹配'
        WHEN pt.pipeline = 'L4_generate_report' THEN 'L4 报告生成'
        ELSE pt.pipeline
    END AS pipeline_label,
    pt.status,
    CASE
        WHEN pt.status = 'pending' THEN '等待中'
        WHEN pt.status = 'running' THEN '运行中'
        WHEN pt.status = 'completed' THEN '已完成'
        WHEN pt.status = 'failed' THEN '失败'
        ELSE pt.status
    END AS status_label,
    pt.progress,
    pt.student_id,
    sp.name AS student_name,
    pt.job_id,
    pt.triggered_by,
    pt.started_at,
    pt.completed_at,
    pt.error_message,
    CASE
        WHEN pt.started_at IS NULL THEN NULL
        WHEN pt.completed_at IS NULL THEN TIMESTAMPDIFF(SECOND, pt.started_at, NOW())
        ELSE TIMESTAMPDIFF(SECOND, pt.started_at, pt.completed_at)
    END AS elapsed_seconds,
    pt.created_at
FROM pipeline_task pt
LEFT JOIN student_profile sp ON pt.student_id = sp.student_id
ORDER BY pt.created_at DESC;

-- ============================================================
-- 验证: 查询视图数量
-- ============================================================
SELECT COUNT(*) AS view_count FROM information_schema.VIEWS
WHERE TABLE_SCHEMA = 'cpa_agent';
