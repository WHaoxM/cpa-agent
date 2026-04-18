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

    INDEX idx_pipeline (pipeline),
    INDEX idx_status (status),
    INDEX idx_student (student_id),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管线任务 (异步任务追踪)';


-- ===========================================
-- 三、初始化完成
-- ===========================================
SELECT 'CPA-Agent 数据库初始化完成' AS status;
