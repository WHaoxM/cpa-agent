-- ===========================================
-- CPA-Agent 数据库初始化脚本
-- 版本: v1.0
-- 创建日期: 2026-04-17
-- ===========================================

-- 创建数据库
CREATE DATABASE IF NOT EXISTS cpa_agent
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE cpa_agent;
SET NAMES utf8mb4;

-- ===========================================
-- 一、核心业务表
-- ===========================================

-- 1.1 student_profile (学生简历 — L0 输出)
CREATE TABLE IF NOT EXISTS student_profile (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL UNIQUE COMMENT '学生唯一ID',
    name                VARCHAR(100) DEFAULT NULL COMMENT '姓名',
    phone               VARCHAR(20)  DEFAULT NULL COMMENT '电话 (脱敏存储)',
    email               VARCHAR(100) DEFAULT NULL COMMENT '邮箱',

    -- 关键可查询字段 (从 JSON 拆出，对应前端 API 字段)
    degree             VARCHAR(20)  DEFAULT NULL COMMENT '最高学历: 本科/硕士/博士',
    school             VARCHAR(100) DEFAULT NULL COMMENT '毕业院校',
    major              VARCHAR(100) DEFAULT NULL COMMENT '专业',
    graduation_year    SMALLINT     DEFAULT NULL COMMENT '毕业年份',

    -- 完整 L0 解析结果 (冻结 Schema 的 16 字段全量)
    profile_json       JSON         NOT NULL COMMENT '完整 student_profile.json',

    -- 数据质量
    data_quality       JSON         DEFAULT NULL COMMENT 'L0 解析质量指标',
    completeness       FLOAT        DEFAULT NULL COMMENT '字段填充率 0-1',

    -- 来源追踪
    source_file        VARCHAR(255) DEFAULT NULL COMMENT '原始简历文件名',
    source_type        VARCHAR(20)  DEFAULT 'pdf' COMMENT 'pdf/docx/txt/json',
    parse_version      VARCHAR(20)  DEFAULT 'v1' COMMENT '解析管线版本',

    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_name (name),
    INDEX idx_degree (degree),
    INDEX idx_school (school),
    INDEX idx_major (major),
    INDEX idx_graduation (graduation_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生简历 (L0 输出)';


-- 1.2 job_posting (岗位信息 — L0 输出)
CREATE TABLE IF NOT EXISTS job_posting (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_id              VARCHAR(64)  NOT NULL UNIQUE COMMENT '岗位唯一ID',
    position_name       VARCHAR(200) NOT NULL COMMENT '岗位名称 (原始)',
    position_normalized VARCHAR(200) DEFAULT NULL COMMENT '标准化岗位名',
    job_category        VARCHAR(50)  DEFAULT NULL COMMENT '岗位大类',

    -- 公司信息 (拆出高频查询字段)
    company_name        VARCHAR(200) DEFAULT NULL COMMENT '公司名',
    company_industry    VARCHAR(100) DEFAULT NULL COMMENT '行业',
    company_size        VARCHAR(50)  DEFAULT NULL COMMENT '规模',

    -- 核心要求 (拆出用于匹配)
    degree_requirement  VARCHAR(20)  DEFAULT NULL COMMENT '学历要求',
    experience_min      SMALLINT     DEFAULT NULL COMMENT '最低经验(年)',
    salary_min         INT          DEFAULT NULL COMMENT '最低薪资(元/月)',
    salary_max         INT          DEFAULT NULL COMMENT '最高薪资(元/月)',

    -- 完整 L0 解析结果
    posting_json        JSON         NOT NULL COMMENT '完整 job_posting.json',

    -- 是否为IT岗位 (R-01 过滤)
    is_it_position      TINYINT(1)   NOT NULL DEFAULT 1 COMMENT 'R-01: 是否IT岗位',

    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_position (position_normalized),
    INDEX idx_category (job_category),
    INDEX idx_company (company_name),
    INDEX idx_degree_req (degree_requirement),
    INDEX idx_is_it (is_it_position)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='岗位信息 (L0 输出)';


-- 1.3 student_portrait_4d (学生四维画像 — L2 输出)
CREATE TABLE IF NOT EXISTS student_portrait_4d (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    target_job_id       VARCHAR(64)  DEFAULT NULL COMMENT '目标岗位ID (D2 覆盖率相对)',

    -- 四维得分 (拆出用于排序/筛选，对应前端 D1-D4 维度)
    d1_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D1 基础要求得分 0-100',
    d2_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D2 职业技能得分 0-100',
    d3_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D3 职业素养得分 0-100',
    d4_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D4 发展潜力得分 0-100',

    -- 综合得分
    competitiveness     FLOAT        NOT NULL DEFAULT 0 COMMENT 'D1×0.25+D2×0.35+D3×0.20+D4×0.20',
    competitiveness_lvl VARCHAR(10)  DEFAULT NULL COMMENT '极强/强/中等/偏弱/弱',
    completeness        FLOAT        NOT NULL DEFAULT 0 COMMENT '字段填充率 0-100',

    -- D2 关键指标
    skill_coverage      FLOAT        DEFAULT NULL COMMENT '技能覆盖率 0-1 (R-03 ≥0.8)',
    skill_count         INT          DEFAULT 0 COMMENT '技能总数',

    -- D3 置信度
    d3_confidence       FLOAT        DEFAULT NULL COMMENT 'D3 Shannon 熵映射 0-1',

    -- 完整画像 JSON (冻结 Schema)
    portrait_json       JSON         NOT NULL COMMENT '完整 student_portrait_4d.json',

    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_student_job (student_id, target_job_id),
    INDEX idx_competitiveness (competitiveness DESC),
    INDEX idx_level (competitiveness_lvl),
    INDEX idx_skill_coverage (skill_coverage)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生四维画像 (L2 输出)';


-- 1.4 job_portrait_4d (岗位四维需求画像 — L2 输出)
CREATE TABLE IF NOT EXISTS job_portrait_4d (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    portrait_id         VARCHAR(64)  NOT NULL UNIQUE COMMENT '画像唯一ID (聚合后)',
    position_name       VARCHAR(200) NOT NULL COMMENT '标准化岗位名',

    -- 聚合统计
    jd_count            INT          NOT NULL DEFAULT 0 COMMENT '聚合的 JD 数量 (R-05 ≥10)',

    -- 核心需求 (拆出)
    required_degree     VARCHAR(20)  DEFAULT NULL COMMENT '学历要求',
    major_strict        TINYINT(1)   DEFAULT 0 COMMENT '是否严格限专业',
    required_skill_cnt  INT          DEFAULT 0 COMMENT '必备技能数',

    -- 维度权重
    d1_weight           FLOAT        NOT NULL DEFAULT 0.25,
    d2_weight           FLOAT        NOT NULL DEFAULT 0.35,
    d3_weight           FLOAT        NOT NULL DEFAULT 0.20,
    d4_weight           FLOAT        NOT NULL DEFAULT 0.20,

    -- 完整画像 JSON (冻结 Schema)
    portrait_json       JSON         NOT NULL COMMENT '完整 job_portrait_4d.json',

    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_position (position_name),
    INDEX idx_jd_count (jd_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='岗位四维需求画像 (L2 输出)';


-- 1.5 match_result (匹配结果 — L3 输出)
CREATE TABLE IF NOT EXISTS match_result (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64)  NOT NULL COMMENT '→ job_portrait_4d.portrait_id',

    -- 四维匹配得分
    d1_match            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D1 匹配得分',
    d2_match            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D2 匹配得分',
    d3_match            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D3 匹配得分',
    d4_match            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D4 匹配得分',
    total_score         FLOAT        NOT NULL DEFAULT 0 COMMENT '加权总分',

    -- 匹配判定
    d1_pass             TINYINT(1)   NOT NULL DEFAULT 0 COMMENT 'D1 硬约束是否通过',
    skill_coverage      FLOAT        DEFAULT NULL COMMENT 'D2 技能覆盖率',
    is_matched          TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '最终是否匹配',
    match_level         VARCHAR(20)  DEFAULT NULL COMMENT '极佳/良好/一般/不匹配',

    -- 完整结果 JSON
    result_json         JSON         NOT NULL COMMENT '完整 match_result.json',

    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_total_score (total_score DESC),
    INDEX idx_is_matched (is_matched),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人岗匹配结果 (L3 输出)';


-- 1.6 skill_gap (技能差距 — L3 输出)
CREATE TABLE IF NOT EXISTS skill_gap (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64)  NOT NULL COMMENT '→ job_portrait_4d.portrait_id',

    -- 差距统计
    critical_count      INT          NOT NULL DEFAULT 0 COMMENT '关键缺失技能数',
    quick_learn_count   INT          NOT NULL DEFAULT 0 COMMENT '快速可学技能数',
    total_gap_count     INT          NOT NULL DEFAULT 0 COMMENT '总差距技能数',

    -- 完整结果 JSON
    gap_json            JSON         NOT NULL COMMENT '完整 skill_gap.json',

    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技能差距分析 (L3 输出)';


-- 1.7 career_path (职业路径推荐 — L3 输出)
CREATE TABLE IF NOT EXISTS career_path (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64)  NOT NULL COMMENT '→ job_portrait_4d.portrait_id',

    -- 推荐结果摘要
    recommended_dir     VARCHAR(200) DEFAULT NULL COMMENT '推荐发展方向',
    promotion_steps     INT          DEFAULT 0 COMMENT '晋升路径步数',
    lateral_count       INT          DEFAULT 0 COMMENT '横向选项数',

    -- 完整结果 JSON
    path_json           JSON         NOT NULL COMMENT '完整 career_path.json',

    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='职业路径推荐 (L3 输出)';


-- 1.8 career_report (职业发展报告 — L4 输出)
CREATE TABLE IF NOT EXISTS career_report (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    report_id           VARCHAR(64)  NOT NULL UNIQUE COMMENT '报告唯一ID',
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64)  NOT NULL COMMENT '→ job_portrait_4d.portrait_id',

    -- 报告元信息
    title               VARCHAR(200) DEFAULT NULL COMMENT '报告标题',
    status              VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/generating/completed/failed',

    -- 6 章内容 (拆列, 支持 Phase 1 骨架 + Phase 2 流式填入)
    ch1_json            JSON DEFAULT NULL COMMENT 'Ch1 个人画像摘要',
    ch2_json            JSON DEFAULT NULL COMMENT 'Ch2 目标岗位分析',
    ch3_json            JSON DEFAULT NULL COMMENT 'Ch3 人岗匹配度分析',
    ch4_json            JSON DEFAULT NULL COMMENT 'Ch4 职业发展路径',
    ch5_json            JSON DEFAULT NULL COMMENT 'Ch5 差距与学习规划',
    ch6_json            JSON DEFAULT NULL COMMENT 'Ch6 评估周期',

    -- 完整报告 JSON
    report_json         JSON DEFAULT NULL COMMENT '完整 career_report.json',

    -- 导出
    html_path           VARCHAR(500) DEFAULT NULL COMMENT '导出 HTML 路径',
    pdf_path            VARCHAR(500) DEFAULT NULL COMMENT '导出 PDF 路径',

    -- 生成耗时
    elapsed_seconds     FLOAT        DEFAULT NULL COMMENT '生成总耗时(秒)',

    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_student (student_id),
    INDEX idx_status (status),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='职业发展报告 (L4 输出)';


-- ===========================================
-- 二、会话与任务表
-- ===========================================

-- 2.1 conversation (对话会话)
CREATE TABLE IF NOT EXISTS conversation (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL UNIQUE COMMENT '会话唯一ID',
    student_id      VARCHAR(64)  DEFAULT NULL COMMENT '关联学生ID (可选)',

    title           VARCHAR(200) DEFAULT NULL COMMENT '会话标题 (自动生成或用户设定)',
    status          VARCHAR(20)  NOT NULL DEFAULT 'active' COMMENT 'active/archived/deleted',

    -- 统计
    message_count   INT          NOT NULL DEFAULT 0 COMMENT '消息总数',
    tool_call_count INT          NOT NULL DEFAULT 0 COMMENT '工具调用总数',

    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_student (student_id),
    INDEX idx_status (status),
    INDEX idx_updated (updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话会话';


-- 2.2 conversation_message (对话消息)
CREATE TABLE IF NOT EXISTS conversation_message (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL COMMENT '→ conversation.session_id',
    message_id      VARCHAR(64)  NOT NULL UNIQUE COMMENT '消息唯一ID',

    role            VARCHAR(20)  NOT NULL COMMENT 'user/assistant/system',
    content         TEXT         NOT NULL COMMENT '消息内容',

    -- Agent 元数据 (仅 assistant 消息)
    tool_calls_json JSON         DEFAULT NULL COMMENT '本次回复的工具调用记录',
    thinking_json   JSON         DEFAULT NULL COMMENT '思考步骤',
    iterations      INT          DEFAULT NULL COMMENT 'ReAct 迭代次数',
    elapsed_seconds FLOAT        DEFAULT NULL COMMENT '响应耗时(秒)',

    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_session (session_id),
    INDEX idx_session_time (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话消息';


-- 2.3 pipeline_task (管线任务)
CREATE TABLE IF NOT EXISTS pipeline_task (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64)  NOT NULL UNIQUE COMMENT '任务唯一ID',

    pipeline        VARCHAR(30)  NOT NULL COMMENT 'L0_parse_resume/L1_build_graph/L2_generate_portrait/L3_run_match/L4_generate_report',
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/running/completed/failed',
    progress        INT          NOT NULL DEFAULT 0 COMMENT '进度 0-100',

    -- 输入参数
    params_json     JSON         DEFAULT NULL COMMENT '管线输入参数',

    -- 输出/错误
    result_json     JSON         DEFAULT NULL COMMENT '管线输出摘要',
    error_message   TEXT         DEFAULT NULL COMMENT '失败时的错误信息',

    -- 关联
    student_id      VARCHAR(64)  DEFAULT NULL COMMENT '关联学生ID',
    job_id          VARCHAR(64)  DEFAULT NULL COMMENT '关联岗位ID',
    triggered_by    VARCHAR(20)  DEFAULT 'api' COMMENT 'api/agent/cron',

    -- 耗时
    started_at      DATETIME     DEFAULT NULL,
    completed_at    DATETIME     DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_pipeline (pipeline),
    INDEX idx_status (status),
    INDEX idx_student (student_id),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管线任务 (异步任务追踪)';


-- ===========================================
-- 三、冷启动知识表 (CS-1)
-- ===========================================

-- 3.1 career_insights (职业方向摘要)
CREATE TABLE IF NOT EXISTS career_insights (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    role_name           VARCHAR(50)  NOT NULL UNIQUE,
    category            VARCHAR(20)  NOT NULL COMMENT '大类',
    sub_category        VARCHAR(50)  DEFAULT NULL COMMENT '子类',
    avg_salary_low      INT          DEFAULT NULL COMMENT '薪资下限K/月',
    avg_salary_high     INT          DEFAULT NULL COMMENT '薪资上限K/月',
    demand_level        VARCHAR(10)  DEFAULT NULL COMMENT '极高/高/中/低',
    hot_cities          JSON         DEFAULT NULL,
    hot_industries      JSON         DEFAULT NULL,
    entry_difficulty    VARCHAR(10)  DEFAULT NULL COMMENT '低/中/高',
    growth_trend        VARCHAR(10)  DEFAULT NULL COMMENT '上升/平稳/下降',
    core_skills         JSON         DEFAULT NULL,
    skill_count         INT          DEFAULT 0,
    description         TEXT         DEFAULT NULL COMMENT '面向学生的通俗解释',
    day_in_life         TEXT         DEFAULT NULL COMMENT '岗位的一天',
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_category (category),
    INDEX idx_demand (demand_level),
    INDEX idx_salary (avg_salary_high DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='职业方向摘要 (冷启动探索)';

-- 3.2 skill_taxonomy (技能分类树)
CREATE TABLE IF NOT EXISTS skill_taxonomy (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    skill_name          VARCHAR(100) NOT NULL UNIQUE,
    category            VARCHAR(50)  NOT NULL COMMENT '大类',
    sub_category        VARCHAR(50)  DEFAULT NULL COMMENT '子类',
    tier                TINYINT      NOT NULL DEFAULT 2 COMMENT '1=基础 2=进阶 3=高级',
    difficulty          VARCHAR(10)  NOT NULL DEFAULT '中' COMMENT '低/中/高',
    weeks_to_learn      INT          DEFAULT NULL,
    prerequisites       JSON         DEFAULT NULL,
    related_roles       JSON         DEFAULT NULL,
    heat_score          FLOAT        DEFAULT 0.5 COMMENT '市场热度0-1',
    dimension           VARCHAR(5)   NOT NULL DEFAULT 'D2' COMMENT 'D2=职业技能 D3=职业素养 D4=发展潜力',
    proficiency_criteria JSON        DEFAULT NULL COMMENT '{"精通":"...","熟练":"...","了解":"...","不会":"..."}',
    description         TEXT         DEFAULT NULL,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_category (category),
    INDEX idx_tier (tier),
    INDEX idx_heat (heat_score DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技能分类树 (冷启动学习路径)';

-- 3.3 learning_path_template (学习路径模板)
CREATE TABLE IF NOT EXISTS learning_path_template (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    path_id             VARCHAR(50)  NOT NULL UNIQUE,
    target_role         VARCHAR(50)  NOT NULL,
    target_level        VARCHAR(20)  NOT NULL DEFAULT '初级',
    steps               JSON         NOT NULL COMMENT '[{order,skill,weeks,resources}]',
    total_weeks         INT          NOT NULL DEFAULT 0,
    description         TEXT         DEFAULT NULL,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_role (target_role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学习路径模板 (冷启动)';

-- 3.4 student_self_assessment (技能自评)
CREATE TABLE IF NOT EXISTS student_self_assessment (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    assessment_id       VARCHAR(64)  NOT NULL UNIQUE,
    student_id          VARCHAR(64)  NOT NULL,
    assessed_skills     JSON         NOT NULL COMMENT '[{skill,level,confidence}]',
    assessed_abilities  JSON         DEFAULT NULL,
    weak_skills         JSON         DEFAULT NULL,
    recommended_roles   JSON         DEFAULT NULL,
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技能自评 (冷启动→画像桥梁)';


-- ===========================================
-- 四、实体归一化知识库 (R-04/R-03)
-- ===========================================

-- 4.1 canonical_entity (规范实体库)
CREATE TABLE IF NOT EXISTS canonical_entity (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_type         VARCHAR(32)  NOT NULL COMMENT 'skill/position/major/certificate/degree/award',
    canonical_name      VARCHAR(128) NOT NULL COMMENT '规范名称',
    category            VARCHAR(64)  DEFAULT NULL COMMENT '分类',
    aliases_json        JSON         DEFAULT NULL COMMENT '别名列表',
    frequency           INT          DEFAULT 1 COMMENT '出现频次',
    source              VARCHAR(32)  DEFAULT 'seed' COMMENT 'seed/oov_auto/oov_review',
    created_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_type_name (entity_type, canonical_name),
    INDEX idx_type_category (entity_type, category),
    INDEX idx_frequency (frequency)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='归一化规范实体库';

-- 4.2 normalization_rule (外部规则表)
CREATE TABLE IF NOT EXISTS normalization_rule (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    rule_domain         VARCHAR(64)  NOT NULL COMMENT '规则域: degree/certificate_alias/award_level/major_alias/skill_alias',
    match_mode          VARCHAR(32)  NOT NULL DEFAULT 'contains' COMMENT 'exact/contains/compact_exact/compact_contains',
    pattern             VARCHAR(255) NOT NULL COMMENT '触发模式',
    canonical_value     VARCHAR(255) NOT NULL COMMENT '归一化结果',
    priority            INT          NOT NULL DEFAULT 100 COMMENT '优先级(大优先)',
    enabled             TINYINT(1)   NOT NULL DEFAULT 1,
    source              VARCHAR(32)  DEFAULT 'seed' COMMENT 'seed/admin/agent_auto',
    created_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_domain_pattern (rule_domain, pattern),
    INDEX idx_domain_priority (rule_domain, priority),
    INDEX idx_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='归一化外部规则表';


-- ===========================================
-- 五、冷启动种子数据 (基于2026招聘市场)
-- ===========================================

SELECT '冷启动表创建完成，种子数据见 seed_cold_start.sql' AS status;


-- ===========================================
-- 六、账号与权限表 (前端 User/ClassData/StudentGrade/SystemStats 对应)
-- 状态: 待冻结 (Pending Freeze)
-- 设计原则: 前端优先 - 字段对齐前端 types/user.ts 接口
-- ===========================================

-- 6.1 user_account (统一账号表)
-- 对齐前端 User 接口: id/username/name/email/avatar/role/status/classId/phone/signature/createdAt
-- 角色体系: student / teacher / admin (对齐 vue-kecheng/AGENTS.md 三角色体验)
CREATE TABLE IF NOT EXISTS user_account (
    id              VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT '用户唯一ID (学生场景下与 student_profile.student_id 对齐)',
    username        VARCHAR(50)  NOT NULL UNIQUE COMMENT '登录用户名',
    password_hash   VARCHAR(255) NOT NULL COMMENT 'bcrypt 哈希',
    name            VARCHAR(100) DEFAULT NULL COMMENT '姓名',
    email           VARCHAR(100) DEFAULT NULL COMMENT '邮箱',
    phone           VARCHAR(20)  DEFAULT NULL COMMENT '电话 (脱敏存储)',
    avatar          VARCHAR(500) DEFAULT NULL COMMENT '头像URL',
    role            VARCHAR(20)  NOT NULL DEFAULT 'student' COMMENT 'student/teacher/admin',
    status          VARCHAR(20)  NOT NULL DEFAULT 'active' COMMENT 'active/disabled',
    class_id        VARCHAR(64)  DEFAULT NULL COMMENT '→ class_info.id (学生所属班级)',
    signature       VARCHAR(255) DEFAULT NULL COMMENT '个性签名',
    last_login_at   DATETIME     DEFAULT NULL COMMENT '最近登录时间',
    last_login_ip   VARCHAR(45)  DEFAULT NULL COMMENT '最近登录IP',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_role (role),
    INDEX idx_status (status),
    INDEX idx_class (class_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='统一账号表 (学生/教师/管理员)';


-- 6.2 class_info (班级信息)
-- 对齐前端 ClassData 接口: id/name/studentCount/courseIds
CREATE TABLE IF NOT EXISTS class_info (
    id              VARCHAR(64)  NOT NULL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL COMMENT '班级名称',
    teacher_id      VARCHAR(64)  DEFAULT NULL COMMENT '→ user_account.id (班主任)',
    course_ids      JSON         DEFAULT NULL COMMENT '关联课程ID列表',
    student_count   INT          NOT NULL DEFAULT 0 COMMENT '学生数 (冗余字段, 定期同步)',
    description     VARCHAR(255) DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_teacher (teacher_id),
    INDEX idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='班级信息';


-- 6.3 student_grade (学生成绩)
-- 对齐前端 StudentGrade 接口: userId/userName/courseId/totalScore/quizScores/completionRate
CREATE TABLE IF NOT EXISTS student_grade (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL COMMENT '→ user_account.id',
    course_id       VARCHAR(64)  NOT NULL COMMENT '→ course.course_id',
    total_score     FLOAT        NOT NULL DEFAULT 0 COMMENT '总分 0-100',
    quiz_scores     JSON         DEFAULT NULL COMMENT '各次小测验分数 [88, 92, 75]',
    completion_rate FLOAT        NOT NULL DEFAULT 0 COMMENT '完成率 0-1',
    graded_at       DATETIME     DEFAULT NULL COMMENT '评分时间',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_course (user_id, course_id),
    INDEX idx_course (course_id),
    INDEX idx_score (total_score DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生成绩';


-- 6.4 active_user_log (用户登录日志)
-- 对齐前端 SystemStats.activeUsers: daily/weekly/monthly 统计来源
CREATE TABLE IF NOT EXISTS active_user_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL COMMENT '→ user_account.id',
    login_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ip              VARCHAR(45)  DEFAULT NULL,
    user_agent      VARCHAR(255) DEFAULT NULL,
    INDEX idx_user (user_id),
    INDEX idx_login (login_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户登录日志 (DAU/WAU/MAU 统计来源)';


-- 6.5 admin_audit_log (管理员操作审计)
-- 所有 admin_bp 下的写操作都应记录到此表
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    admin_id        VARCHAR(64)  NOT NULL COMMENT '→ user_account.id',
    action          VARCHAR(50)  NOT NULL COMMENT 'create/update/delete/upload/approve/reject/login',
    target_type     VARCHAR(50)  NOT NULL COMMENT 'knowledge_base/dataset/oov/user/config',
    target_id       VARCHAR(64)  DEFAULT NULL,
    detail_json     JSON         DEFAULT NULL COMMENT '操作详情',
    ip              VARCHAR(45)  DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_admin (admin_id),
    INDEX idx_action (action),
    INDEX idx_target (target_type, target_id),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员操作审计日志';


-- ===========================================
-- 七、管理员端业务表 (被 admin.py / admin_kb.py 引用)
-- 状态: 待冻结 (Pending Freeze)
-- 说明: 这些表此前被 API 引用但未在 init_mysql.sql 创建, 导致 API 调用直接报错
-- ===========================================

-- 7.1 knowledge_base (知识库上传记录)
-- 被 admin.py list_knowledge_base / upload_knowledge_base / delete_knowledge_base 引用
-- 被 admin_kb.py smart_upload_knowledge 引用
CREATE TABLE IF NOT EXISTS knowledge_base (
    id              VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT '如 kb_xxxxxxxxxxxx',
    title           VARCHAR(200) NOT NULL COMMENT '标题',
    type            VARCHAR(30)  NOT NULL DEFAULT 'text' COMMENT 'text/skill/jd/file',
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/processing/completed/failed',
    file_path       VARCHAR(500) DEFAULT NULL COMMENT '上传文件路径',
    file_size       BIGINT       DEFAULT NULL COMMENT '字节',
    content_text    LONGTEXT     DEFAULT NULL COMMENT '原始内容文本',
    parsed_entities JSON         DEFAULT NULL COMMENT '解析出的实体 (skills/roles/relations)',
    intent_json     JSON         DEFAULT NULL COMMENT '意图识别结果',
    uploaded_by     VARCHAR(64)  DEFAULT NULL COMMENT '→ user_account.id',
    error_message   TEXT         DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_type (type),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库上传记录';


-- 7.2 job_dataset (岗位数据集上传记录)
-- 被 admin.py list_dataset / upload_dataset / delete_dataset 引用
CREATE TABLE IF NOT EXISTS job_dataset (
    id              VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT '如 ds_xxxxxxxxxxxx',
    title           VARCHAR(200) NOT NULL,
    count           INT          NOT NULL DEFAULT 0 COMMENT '岗位数量',
    category        VARCHAR(50)  DEFAULT NULL COMMENT '数据集分类',
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/processing/completed/failed',
    file_path       VARCHAR(500) DEFAULT NULL,
    file_size       BIGINT       DEFAULT NULL,
    description     TEXT         DEFAULT NULL,
    uploaded_by     VARCHAR(64)  DEFAULT NULL COMMENT '→ user_account.id',
    error_message   TEXT         DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_category (category),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='岗位数据集上传记录';


-- 7.3 oov_entity (OOV 实体审核队列)
-- 被 admin.py list_oov_pending / approve_oov / reject_oov 引用
-- 字段对齐 admin.py 中的实际访问: oov_id/raw_entity/entity_type/context_json/source/frequency
--                  /status/canonical_mapping/reviewed_by/reviewed_at/review_notes
CREATE TABLE IF NOT EXISTS oov_entity (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    oov_id              VARCHAR(64)  NOT NULL UNIQUE COMMENT '如 oov_xxxxxxxxxxxx',
    raw_entity          VARCHAR(200) NOT NULL COMMENT '原始未识别实体',
    entity_type         VARCHAR(32)  NOT NULL COMMENT 'skill/position/major/certificate/degree/award',
    context_json        JSON         DEFAULT NULL COMMENT '上下文 (出现位置/句子)',
    source              VARCHAR(64)  DEFAULT NULL COMMENT '来源: 简历ID/JD ID/知识库ID',
    frequency           INT          NOT NULL DEFAULT 1 COMMENT '出现频次',
    status              VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/approved/rejected',
    canonical_mapping   VARCHAR(200) DEFAULT NULL COMMENT '→ canonical_entity.canonical_name (审核后)',
    reviewed_by         VARCHAR(64)  DEFAULT NULL COMMENT '→ user_account.id',
    reviewed_at         DATETIME     DEFAULT NULL,
    review_notes        TEXT         DEFAULT NULL COMMENT '审核备注 (拒绝原因等)',
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_type (entity_type),
    INDEX idx_source (source),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='OOV 实体审核队列';


-- ===========================================
-- 八、API 扩展表 (前端收藏/目标岗位/课程/章节/学习进度)
-- ===========================================

-- 8.1 saved_job (收藏岗位)
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

-- 8.2 target_role (目标岗位)
CREATE TABLE IF NOT EXISTS target_role (
    id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id            VARCHAR(64)  NOT NULL COMMENT '用户ID',
    role_name          VARCHAR(200) NOT NULL COMMENT '目标岗位名称',
    priority           INT          NOT NULL DEFAULT 1 COMMENT '优先级',
    salary_expectation VARCHAR(100) DEFAULT NULL COMMENT '薪资期望',
    location_preference VARCHAR(200) DEFAULT NULL COMMENT '地点偏好',
    notes              TEXT         DEFAULT NULL COMMENT '备注',
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_user (user_id),
    INDEX idx_priority (priority DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='目标岗位';

-- 8.3 course (课程)
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

-- 8.4 chapter (课程章节)
-- 注: learning_progress.chapter_id 引用 chapter.id (转字符串存储)
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

-- 8.5 learning_progress (学习进度)
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


-- ===========================================
-- 九、初始化完成
-- ===========================================
SELECT 'CPA-Agent 数据库初始化完成 (含账号/权限/管理员端表/API扩展表)' AS status;
