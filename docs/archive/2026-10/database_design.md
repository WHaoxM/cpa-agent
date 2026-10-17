> Archived on 2026-10-17. Historical schema design, not the current database migration authority.
> Superseded by: `backend/migrations/`, `docs/adr/0008-versioned-mysql-schema-migrations.md`, and `docs/runbooks/mysql-migrations.md`.

# CPA-Agent 关系型数据库设计文档

> **项目**: CPA-Agent 大学生职业规划智能体
> **数据库**: MySQL 8.0 / utf8mb4
> **文档版本**: v2.0
> **更新日期**: 2026-07-12
> **表总数**: 30 张

---

## 一、设计概述

### 1.1 设计原则

1. **Hybrid 存储模式**: 高频查询字段拆列建索引 + 完整 JSON 保留原始结构,兼顾查询性能与数据完整性
2. **分层组织**: 按 L0→L1→L2→L3→L4 数据管线层级组织核心业务表
3. **四维画像模型**: D1(基础要求)+ D2(职业技能)+ D3(职业素养)+ D4(发展潜力)贯穿学生画像、岗位画像、匹配结果全链路
4. **统一角色体系**: 学生 / 教师 / 管理员三角色,通过 user_account 统一身份层管理
5. **Schema 冻结**: 核心表结构冻结,只允许加列/加索引,不允许修改已有结构或删列

### 1.2 存储引擎与字符集

| 配置项 | 值 |
|---|---|
| 存储引擎 | InnoDB(支持事务、外键、行级锁) |
| 字符集 | utf8mb4(支持完整 Unicode,包括 emoji) |
| 排序规则 | utf8mb4_unicode_ci |
| 主键策略 | BIGINT 自增 或 VARCHAR(64) 业务 ID |

### 1.3 数据库连接配置

```ini
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=********
MYSQL_DATABASE=cpa_agent
```

### 1.4 表分类总览

| 分类 | 表数量 | 说明 |
|---|---|---|
| 一、核心业务表 | 8 张 | L0-L4 管线输出 |
| 二、会话与任务表 | 3 张 | Agent 对话与异步任务 |
| 三、冷启动知识表 | 4 张 | 系统初始化数据 |
| 四、实体归一化表 | 2 张 | OOV 实体处理 |
| 五、账号与权限表 | 5 张 | 用户/班级/成绩/审计 |
| 六、管理员端业务表 | 3 张 | 知识库/数据集/OOV 审核 |
| 七、API 扩展表 | 5 张 | 收藏/课程/学习进度 |
| **合计** | **30 张** | |

---

## 二、核心业务表(L0-L4 管线输出)

### 2.1 student_profile(学生简历 — L0 输出)

**用途**: 存储学生简历解析结果,采用 Hybrid 模式(关键字段拆列 + 完整 JSON)

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| student_id | VARCHAR(64) | 否 | - | 学生唯一 ID(业务主键) |
| name | VARCHAR(100) | 是 | NULL | 姓名 |
| phone | VARCHAR(20) | 是 | NULL | 电话(脱敏存储) |
| email | VARCHAR(100) | 是 | NULL | 邮箱 |
| degree | VARCHAR(20) | 是 | NULL | 最高学历: 本科/硕士/博士 |
| school | VARCHAR(100) | 是 | NULL | 毕业院校 |
| major | VARCHAR(100) | 是 | NULL | 专业 |
| graduation_year | SMALLINT | 是 | NULL | 毕业年份 |
| profile_json | JSON | 否 | - | 完整 student_profile.json |
| data_quality | JSON | 是 | NULL | L0 解析质量指标 |
| completeness | FLOAT | 是 | NULL | 字段填充率 0-1 |
| source_file | VARCHAR(255) | 是 | NULL | 原始简历文件名 |
| source_type | VARCHAR(20) | 是 | 'pdf' | pdf/docx/txt/json |
| parse_version | VARCHAR(20) | 是 | 'v1' | 解析管线版本 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `student_id` (student_id)
- INDEX `idx_name` (name)
- INDEX `idx_degree` (degree)
- INDEX `idx_school` (school)
- INDEX `idx_major` (major)
- INDEX `idx_graduation` (graduation_year)

```sql
CREATE TABLE student_profile (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id      VARCHAR(64)  NOT NULL UNIQUE COMMENT '学生唯一ID',
    name            VARCHAR(100) DEFAULT NULL COMMENT '姓名',
    phone           VARCHAR(20)  DEFAULT NULL COMMENT '电话 (脱敏存储)',
    email           VARCHAR(100) DEFAULT NULL COMMENT '邮箱',
    degree          VARCHAR(20)  DEFAULT NULL COMMENT '最高学历: 本科/硕士/博士',
    school          VARCHAR(100) DEFAULT NULL COMMENT '毕业院校',
    major           VARCHAR(100) DEFAULT NULL COMMENT '专业',
    graduation_year SMALLINT     DEFAULT NULL COMMENT '毕业年份',
    profile_json    JSON         NOT NULL     COMMENT '完整 student_profile.json',
    data_quality    JSON         DEFAULT NULL COMMENT 'L0 解析质量指标',
    completeness    FLOAT        DEFAULT NULL COMMENT '字段填充率 0-1',
    source_file     VARCHAR(255) DEFAULT NULL COMMENT '原始简历文件名',
    source_type     VARCHAR(20)  DEFAULT 'pdf' COMMENT 'pdf/docx/txt/json',
    parse_version   VARCHAR(20)  DEFAULT 'v1' COMMENT '解析管线版本',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_name (name),
    INDEX idx_degree (degree),
    INDEX idx_school (school),
    INDEX idx_major (major),
    INDEX idx_graduation (graduation_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生简历 (L0 输出)';
```

---

### 2.2 job_posting(岗位信息 — L0 输出)

**用途**: 存储岗位 JD 解析结果

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| job_id | VARCHAR(64) | 否 | - | 岗位唯一 ID |
| position_name | VARCHAR(200) | 否 | - | 岗位名称(原始) |
| position_normalized | VARCHAR(200) | 是 | NULL | 标准化岗位名 |
| job_category | VARCHAR(50) | 是 | NULL | 岗位大类 |
| company_name | VARCHAR(200) | 是 | NULL | 公司名 |
| company_industry | VARCHAR(100) | 是 | NULL | 行业 |
| company_size | VARCHAR(50) | 是 | NULL | 规模 |
| degree_requirement | VARCHAR(20) | 是 | NULL | 学历要求 |
| experience_min | SMALLINT | 是 | NULL | 最低经验(年) |
| salary_min | INT | 是 | NULL | 最低薪资(元/月) |
| salary_max | INT | 是 | NULL | 最高薪资(元/月) |
| posting_json | JSON | 否 | - | 完整 job_posting.json |
| is_it_position | TINYINT(1) | 否 | 1 | R-01: 是否 IT 岗位 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `job_id` (job_id)
- INDEX `idx_position` (position_normalized)
- INDEX `idx_category` (job_category)
- INDEX `idx_company` (company_name)
- INDEX `idx_degree_req` (degree_requirement)
- INDEX `idx_is_it` (is_it_position)

```sql
CREATE TABLE job_posting (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_id              VARCHAR(64)  NOT NULL UNIQUE COMMENT '岗位唯一ID',
    position_name       VARCHAR(200) NOT NULL        COMMENT '岗位名称 (原始)',
    position_normalized VARCHAR(200) DEFAULT NULL     COMMENT '标准化岗位名',
    job_category        VARCHAR(50)  DEFAULT NULL     COMMENT '岗位大类',
    company_name        VARCHAR(200) DEFAULT NULL COMMENT '公司名',
    company_industry    VARCHAR(100) DEFAULT NULL COMMENT '行业',
    company_size        VARCHAR(50)  DEFAULT NULL COMMENT '规模',
    degree_requirement  VARCHAR(20)  DEFAULT NULL COMMENT '学历要求',
    experience_min      SMALLINT     DEFAULT NULL COMMENT '最低经验(年)',
    salary_min          INT          DEFAULT NULL COMMENT '最低薪资(元/月)',
    salary_max          INT          DEFAULT NULL COMMENT '最高薪资(元/月)',
    posting_json        JSON         NOT NULL     COMMENT '完整 job_posting.json',
    is_it_position      TINYINT(1)   NOT NULL DEFAULT 1 COMMENT 'R-01: 是否IT岗位',
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_position (position_normalized),
    INDEX idx_category (job_category),
    INDEX idx_company (company_name),
    INDEX idx_degree_req (degree_requirement),
    INDEX idx_is_it (is_it_position)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='岗位信息 (L0 输出)';
```

---

### 2.3 student_portrait_4d(学生四维画像 — L2 输出)

**用途**: 存储学生 D1-D4 四维画像评分

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| student_id | VARCHAR(64) | 否 | - | → student_profile.student_id |
| target_job_id | VARCHAR(64) | 是 | NULL | 目标岗位 ID(D2 覆盖率相对) |
| d1_score | FLOAT | 否 | 0 | D1 基础要求得分 0-100 |
| d2_score | FLOAT | 否 | 0 | D2 职业技能得分 0-100 |
| d3_score | FLOAT | 否 | 0 | D3 职业素养得分 0-100 |
| d4_score | FLOAT | 否 | 0 | D4 发展潜力得分 0-100 |
| competitiveness | FLOAT | 否 | 0 | 综合得分 D1×0.25+D2×0.35+D3×0.20+D4×0.20 |
| competitiveness_lvl | VARCHAR(10) | 是 | NULL | 极强/强/中等/偏弱/弱 |
| completeness | FLOAT | 否 | 0 | 字段填充率 0-100 |
| skill_coverage | FLOAT | 是 | NULL | 技能覆盖率 0-1(R-03 ≥0.8) |
| skill_count | INT | 否 | 0 | 技能总数 |
| d3_confidence | FLOAT | 是 | NULL | D3 Shannon 熵映射 0-1 |
| portrait_json | JSON | 否 | - | 完整 student_portrait_4d.json |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `uk_student_job` (student_id, target_job_id)
- INDEX `idx_competitiveness` (competitiveness DESC)
- INDEX `idx_level` (competitiveness_lvl)
- INDEX `idx_skill_coverage` (skill_coverage)

```sql
CREATE TABLE student_portrait_4d (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    target_job_id       VARCHAR(64)  DEFAULT NULL COMMENT '目标岗位ID (D2 覆盖率相对)',
    d1_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D1 基础要求得分 0-100',
    d2_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D2 职业技能得分 0-100',
    d3_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D3 职业素养得分 0-100',
    d4_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D4 发展潜力得分 0-100',
    competitiveness     FLOAT        NOT NULL DEFAULT 0 COMMENT 'D1×0.25+D2×0.35+D3×0.20+D4×0.20',
    competitiveness_lvl VARCHAR(10)  DEFAULT NULL       COMMENT '极强/强/中等/偏弱/弱',
    completeness        FLOAT        NOT NULL DEFAULT 0 COMMENT '字段填充率 0-100',
    skill_coverage      FLOAT        DEFAULT NULL COMMENT '技能覆盖率 0-1 (R-03 ≥0.8)',
    skill_count         INT          DEFAULT 0    COMMENT '技能总数',
    d3_confidence       FLOAT        DEFAULT NULL COMMENT 'D3 Shannon 熵映射 0-1',
    portrait_json       JSON         NOT NULL     COMMENT '完整 student_portrait_4d.json',
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_student_job (student_id, target_job_id),
    INDEX idx_competitiveness (competitiveness DESC),
    INDEX idx_level (competitiveness_lvl),
    INDEX idx_skill_coverage (skill_coverage)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生四维画像 (L2 输出)';
```

---

### 2.4 job_portrait_4d(岗位四维需求画像 — L2 输出)

**用途**: 存储岗位聚合画像(≥10 JD 聚合)

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| portrait_id | VARCHAR(64) | 否 | - | 画像唯一 ID |
| position_name | VARCHAR(200) | 否 | - | 标准化岗位名 |
| jd_count | INT | 否 | 0 | 聚合的 JD 数量(R-05 ≥10) |
| required_degree | VARCHAR(20) | 是 | NULL | 学历要求 |
| major_strict | TINYINT(1) | 否 | 0 | 是否严格限专业 |
| required_skill_cnt | INT | 否 | 0 | 必备技能数 |
| d1_weight | FLOAT | 否 | 0.25 | D1 权重 |
| d2_weight | FLOAT | 否 | 0.35 | D2 权重 |
| d3_weight | FLOAT | 否 | 0.20 | D3 权重 |
| d4_weight | FLOAT | 否 | 0.20 | D4 权重 |
| portrait_json | JSON | 否 | - | 完整 job_portrait_4d.json |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `portrait_id` (portrait_id)
- INDEX `idx_position` (position_name)
- INDEX `idx_jd_count` (jd_count)

```sql
CREATE TABLE job_portrait_4d (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    portrait_id         VARCHAR(64)  NOT NULL UNIQUE COMMENT '画像唯一ID (聚合后)',
    position_name       VARCHAR(200) NOT NULL        COMMENT '标准化岗位名',
    jd_count            INT          NOT NULL DEFAULT 0 COMMENT '聚合的 JD 数量 (R-05 ≥10)',
    required_degree     VARCHAR(20)  DEFAULT NULL COMMENT '学历要求',
    major_strict        TINYINT(1)   DEFAULT 0    COMMENT '是否严格限专业',
    required_skill_cnt  INT          DEFAULT 0    COMMENT '必备技能数',
    d1_weight           FLOAT        NOT NULL DEFAULT 0.25,
    d2_weight           FLOAT        NOT NULL DEFAULT 0.35,
    d3_weight           FLOAT        NOT NULL DEFAULT 0.20,
    d4_weight           FLOAT        NOT NULL DEFAULT 0.20,
    portrait_json       JSON         NOT NULL COMMENT '完整 job_portrait_4d.json',
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_position (position_name),
    INDEX idx_jd_count (jd_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='岗位四维需求画像 (L2 输出)';
```

---

### 2.5 match_result(人岗匹配结果 — L3 输出)

**用途**: 存储学生与岗位的匹配结果

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| student_id | VARCHAR(64) | 否 | - | → student_profile.student_id |
| job_portrait_id | VARCHAR(64) | 否 | - | → job_portrait_4d.portrait_id |
| d1_match | FLOAT | 否 | 0 | D1 匹配得分 |
| d2_match | FLOAT | 否 | 0 | D2 匹配得分 |
| d3_match | FLOAT | 否 | 0 | D3 匹配得分 |
| d4_match | FLOAT | 否 | 0 | D4 匹配得分 |
| total_score | FLOAT | 否 | 0 | 加权总分 |
| d1_pass | TINYINT(1) | 否 | 0 | D1 硬约束是否通过 |
| skill_coverage | FLOAT | 是 | NULL | D2 技能覆盖率 |
| is_matched | TINYINT(1) | 否 | 0 | 最终是否匹配 |
| match_level | VARCHAR(20) | 是 | NULL | 极佳/良好/一般/不匹配 |
| result_json | JSON | 否 | - | 完整 match_result.json |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |

**索引**:
- UNIQUE `uk_student_job` (student_id, job_portrait_id)
- INDEX `idx_total_score` (total_score DESC)
- INDEX `idx_is_matched` (is_matched)
- INDEX `idx_student` (student_id)

```sql
CREATE TABLE match_result (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64) NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64) NOT NULL COMMENT '→ job_portrait_4d.portrait_id',
    d1_match            FLOAT       NOT NULL DEFAULT 0 COMMENT 'D1 匹配得分',
    d2_match            FLOAT       NOT NULL DEFAULT 0 COMMENT 'D2 匹配得分',
    d3_match            FLOAT       NOT NULL DEFAULT 0 COMMENT 'D3 匹配得分',
    d4_match            FLOAT       NOT NULL DEFAULT 0 COMMENT 'D4 匹配得分',
    total_score         FLOAT       NOT NULL DEFAULT 0 COMMENT '加权总分',
    d1_pass             TINYINT(1)  NOT NULL DEFAULT 0 COMMENT 'D1 硬约束是否通过',
    skill_coverage      FLOAT       DEFAULT NULL       COMMENT 'D2 技能覆盖率',
    is_matched          TINYINT(1)  NOT NULL DEFAULT 0 COMMENT '最终是否匹配',
    match_level         VARCHAR(20) DEFAULT NULL        COMMENT '极佳/良好/一般/不匹配',
    result_json         JSON        NOT NULL COMMENT '完整 match_result.json',
    created_at          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_total_score (total_score DESC),
    INDEX idx_is_matched (is_matched),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人岗匹配结果 (L3 输出)';
```

---

### 2.6 skill_gap(技能差距分析 — L3 输出)

**用途**: 存储学生与目标岗位的技能差距

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| student_id | VARCHAR(64) | 否 | - | → student_profile.student_id |
| job_portrait_id | VARCHAR(64) | 否 | - | → job_portrait_4d.portrait_id |
| critical_count | INT | 否 | 0 | 关键缺失技能数 |
| quick_learn_count | INT | 否 | 0 | 快速可学技能数 |
| total_gap_count | INT | 否 | 0 | 总差距技能数 |
| gap_json | JSON | 否 | - | 完整 skill_gap.json |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |

**索引**:
- UNIQUE `uk_student_job` (student_id, job_portrait_id)
- INDEX `idx_student` (student_id)

```sql
CREATE TABLE skill_gap (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64) NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64) NOT NULL COMMENT '→ job_portrait_4d.portrait_id',
    critical_count      INT         NOT NULL DEFAULT 0 COMMENT '关键缺失技能数',
    quick_learn_count   INT         NOT NULL DEFAULT 0 COMMENT '快速可学技能数',
    total_gap_count     INT         NOT NULL DEFAULT 0 COMMENT '总差距技能数',
    gap_json            JSON        NOT NULL COMMENT '完整 skill_gap.json',
    created_at          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技能差距分析 (L3 输出)';
```

---

### 2.7 career_path(职业路径推荐 — L3 输出)

**用途**: 存储职业发展路径推荐

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| student_id | VARCHAR(64) | 否 | - | → student_profile.student_id |
| job_portrait_id | VARCHAR(64) | 否 | - | → job_portrait_4d.portrait_id |
| recommended_dir | VARCHAR(200) | 是 | NULL | 推荐发展方向 |
| promotion_steps | INT | 否 | 0 | 晋升路径步数 |
| lateral_count | INT | 否 | 0 | 横向选项数 |
| path_json | JSON | 否 | - | 完整 career_path.json |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |

**索引**:
- UNIQUE `uk_student_job` (student_id, job_portrait_id)
- INDEX `idx_student` (student_id)

```sql
CREATE TABLE career_path (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64)  NOT NULL COMMENT '→ job_portrait_4d.portrait_id',
    recommended_dir     VARCHAR(200) DEFAULT NULL COMMENT '推荐发展方向',
    promotion_steps     INT          DEFAULT 0    COMMENT '晋升路径步数',
    lateral_count       INT          DEFAULT 0    COMMENT '横向选项数',
    path_json           JSON         NOT NULL COMMENT '完整 career_path.json',
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='职业路径推荐 (L3 输出)';
```

---

### 2.8 career_report(职业发展报告 — L4 输出)

**用途**: 存储 6 章节职业发展报告,支持流式生成
**设计特点**: 6 章内容拆列存储(ch1-ch6_json),支持 Phase 1 骨架 + Phase 2 流式填入

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| report_id | VARCHAR(64) | 否 | - | 报告唯一 ID |
| student_id | VARCHAR(64) | 否 | - | → student_profile.student_id |
| job_portrait_id | VARCHAR(64) | 否 | - | → job_portrait_4d.portrait_id |
| title | VARCHAR(200) | 是 | NULL | 报告标题 |
| status | VARCHAR(20) | 否 | 'pending' | pending/generating/completed/failed |
| ch1_json | JSON | 是 | NULL | Ch1 个人画像摘要 |
| ch2_json | JSON | 是 | NULL | Ch2 目标岗位分析 |
| ch3_json | JSON | 是 | NULL | Ch3 人岗匹配度分析 |
| ch4_json | JSON | 是 | NULL | Ch4 职业发展路径 |
| ch5_json | JSON | 是 | NULL | Ch5 差距与学习规划 |
| ch6_json | JSON | 是 | NULL | Ch6 评估周期 |
| report_json | JSON | 是 | NULL | 完整 career_report.json |
| html_path | VARCHAR(500) | 是 | NULL | 导出 HTML 路径 |
| pdf_path | VARCHAR(500) | 是 | NULL | 导出 PDF 路径 |
| elapsed_seconds | FLOAT | 是 | NULL | 生成总耗时(秒) |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `report_id` (report_id)
- INDEX `idx_student` (student_id)
- INDEX `idx_status` (status)
- INDEX `idx_created` (created_at DESC)

```sql
CREATE TABLE career_report (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    report_id           VARCHAR(64)  NOT NULL UNIQUE COMMENT '报告唯一ID',
    student_id          VARCHAR(64)  NOT NULL        COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64)  NOT NULL        COMMENT '→ job_portrait_4d.portrait_id',
    title               VARCHAR(200) DEFAULT NULL COMMENT '报告标题',
    status              VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/generating/completed/failed',
    ch1_json            JSON DEFAULT NULL COMMENT 'Ch1 个人画像摘要',
    ch2_json            JSON DEFAULT NULL COMMENT 'Ch2 目标岗位分析',
    ch3_json            JSON DEFAULT NULL COMMENT 'Ch3 人岗匹配度分析',
    ch4_json            JSON DEFAULT NULL COMMENT 'Ch4 职业发展路径',
    ch5_json            JSON DEFAULT NULL COMMENT 'Ch5 差距与学习规划',
    ch6_json            JSON DEFAULT NULL COMMENT 'Ch6 评估周期',
    report_json         JSON DEFAULT NULL COMMENT '完整 career_report.json',
    html_path           VARCHAR(500) DEFAULT NULL COMMENT '导出 HTML 路径',
    pdf_path            VARCHAR(500) DEFAULT NULL COMMENT '导出 PDF 路径',
    elapsed_seconds     FLOAT        DEFAULT NULL COMMENT '生成总耗时(秒)',
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_student (student_id),
    INDEX idx_status (status),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='职业发展报告 (L4 输出)';
```

---

## 三、会话与任务表

### 3.1 conversation(对话会话)

**用途**: 存储 Agent 对话会话

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| session_id | VARCHAR(64) | 否 | - | 会话唯一 ID |
| student_id | VARCHAR(64) | 是 | NULL | 关联学生 ID(可选) |
| title | VARCHAR(200) | 是 | NULL | 会话标题 |
| status | VARCHAR(20) | 否 | 'active' | active/archived/deleted |
| message_count | INT | 否 | 0 | 消息总数 |
| tool_call_count | INT | 否 | 0 | 工具调用总数 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `session_id` (session_id)
- INDEX `idx_student` (student_id)
- INDEX `idx_status` (status)
- INDEX `idx_updated` (updated_at DESC)

```sql
CREATE TABLE conversation (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL UNIQUE COMMENT '会话唯一ID',
    student_id      VARCHAR(64)  DEFAULT NULL    COMMENT '关联学生ID (可选)',
    title           VARCHAR(200) DEFAULT NULL COMMENT '会话标题',
    status          VARCHAR(20)  NOT NULL DEFAULT 'active' COMMENT 'active/archived/deleted',
    message_count   INT          NOT NULL DEFAULT 0 COMMENT '消息总数',
    tool_call_count INT          NOT NULL DEFAULT 0 COMMENT '工具调用总数',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_student (student_id),
    INDEX idx_status (status),
    INDEX idx_updated (updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话会话';
```

---

### 3.2 conversation_message(对话消息)

**用途**: 存储对话消息明细,包含 Agent 元数据

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| session_id | VARCHAR(64) | 否 | - | → conversation.session_id |
| message_id | VARCHAR(64) | 否 | - | 消息唯一 ID |
| role | VARCHAR(20) | 否 | - | user/assistant/system |
| content | TEXT | 否 | - | 消息内容 |
| tool_calls_json | JSON | 是 | NULL | 本次回复的工具调用记录 |
| thinking_json | JSON | 是 | NULL | 思考步骤 |
| iterations | INT | 是 | NULL | ReAct 迭代次数 |
| elapsed_seconds | FLOAT | 是 | NULL | 响应耗时(秒) |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |

**索引**:
- UNIQUE `message_id` (message_id)
- INDEX `idx_session` (session_id)
- INDEX `idx_session_time` (session_id, created_at)

```sql
CREATE TABLE conversation_message (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL        COMMENT '→ conversation.session_id',
    message_id      VARCHAR(64)  NOT NULL UNIQUE COMMENT '消息唯一ID',
    role            VARCHAR(20)  NOT NULL COMMENT 'user/assistant/system',
    content         TEXT         NOT NULL COMMENT '消息内容',
    tool_calls_json JSON         DEFAULT NULL COMMENT '本次回复的工具调用记录',
    thinking_json   JSON         DEFAULT NULL COMMENT '思考步骤',
    iterations      INT          DEFAULT NULL COMMENT 'ReAct 迭代次数',
    elapsed_seconds FLOAT        DEFAULT NULL COMMENT '响应耗时(秒)',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id),
    INDEX idx_session_time (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话消息';
```

---

### 3.3 pipeline_task(管线任务)

**用途**: 异步任务追踪(L0-L4 管线执行)

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| task_id | VARCHAR(64) | 否 | - | 任务唯一 ID |
| pipeline | VARCHAR(30) | 否 | - | L0_parse_resume/L1_build_graph/L2_generate_portrait/L3_run_match/L4_generate_report |
| status | VARCHAR(20) | 否 | 'pending' | pending/running/completed/failed |
| progress | INT | 否 | 0 | 进度 0-100 |
| params_json | JSON | 是 | NULL | 管线输入参数 |
| result_json | JSON | 是 | NULL | 管线输出摘要 |
| error_message | TEXT | 是 | NULL | 失败时的错误信息 |
| student_id | VARCHAR(64) | 是 | NULL | 关联学生 ID |
| job_id | VARCHAR(64) | 是 | NULL | 关联岗位 ID |
| triggered_by | VARCHAR(20) | 是 | 'api' | api/agent/cron |
| started_at | DATETIME | 是 | NULL | 开始时间 |
| completed_at | DATETIME | 是 | NULL | 完成时间 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `task_id` (task_id)
- INDEX `idx_pipeline` (pipeline)
- INDEX `idx_status` (status)
- INDEX `idx_student` (student_id)
- INDEX `idx_created` (created_at DESC)

```sql
CREATE TABLE pipeline_task (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64)  NOT NULL UNIQUE COMMENT '任务唯一ID',
    pipeline        VARCHAR(30)  NOT NULL COMMENT 'L0_parse_resume/L1_build_graph/L2_generate_portrait/L3_run_match/L4_generate_report',
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/running/completed/failed',
    progress        INT          NOT NULL DEFAULT 0 COMMENT '进度 0-100',
    params_json     JSON         DEFAULT NULL COMMENT '管线输入参数',
    result_json     JSON         DEFAULT NULL COMMENT '管线输出摘要',
    error_message   TEXT         DEFAULT NULL COMMENT '失败时的错误信息',
    student_id      VARCHAR(64)  DEFAULT NULL COMMENT '关联学生ID',
    job_id          VARCHAR(64)  DEFAULT NULL COMMENT '关联岗位ID',
    triggered_by    VARCHAR(20)  DEFAULT 'api' COMMENT 'api/agent/cron',
    started_at      DATETIME     DEFAULT NULL,
    completed_at    DATETIME     DEFAULT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_pipeline (pipeline),
    INDEX idx_status (status),
    INDEX idx_student (student_id),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管线任务 (异步任务追踪)';
```

---

## 四、冷启动知识表

### 4.1 career_insights(职业方向摘要)

**用途**: 冷启动职业探索数据,面向学生的通俗解释

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| role_name | VARCHAR(50) | 否 | - | 角色名(唯一) |
| category | VARCHAR(20) | 否 | - | 大类 |
| sub_category | VARCHAR(50) | 是 | NULL | 子类 |
| avg_salary_low | INT | 是 | NULL | 薪资下限 K/月 |
| avg_salary_high | INT | 是 | NULL | 薪资上限 K/月 |
| demand_level | VARCHAR(10) | 是 | NULL | 极高/高/中/低 |
| hot_cities | JSON | 是 | NULL | 热门城市 |
| hot_industries | JSON | 是 | NULL | 热门行业 |
| entry_difficulty | VARCHAR(10) | 是 | NULL | 低/中/高 |
| growth_trend | VARCHAR(10) | 是 | NULL | 上升/平稳/下降 |
| core_skills | JSON | 是 | NULL | 核心技能 |
| skill_count | INT | 否 | 0 | 技能数 |
| description | TEXT | 是 | NULL | 面向学生的通俗解释 |
| day_in_life | TEXT | 是 | NULL | 岗位的一天 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `role_name` (role_name)
- INDEX `idx_category` (category)
- INDEX `idx_demand` (demand_level)
- INDEX `idx_salary` (avg_salary_high DESC)

```sql
CREATE TABLE career_insights (
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
```

---

### 4.2 skill_taxonomy(技能分类树)

**用途**: 冷启动学习路径,技能分类体系

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| skill_name | VARCHAR(100) | 否 | - | 技能名(唯一) |
| category | VARCHAR(50) | 否 | - | 大类 |
| sub_category | VARCHAR(50) | 是 | NULL | 子类 |
| tier | TINYINT | 否 | 2 | 1=基础 2=进阶 3=高级 |
| difficulty | VARCHAR(10) | 否 | '中' | 低/中/高 |
| weeks_to_learn | INT | 是 | NULL | 学习周数 |
| prerequisites | JSON | 是 | NULL | 前置技能 |
| related_roles | JSON | 是 | NULL | 相关岗位 |
| heat_score | FLOAT | 是 | 0.5 | 市场热度 0-1 |
| dimension | VARCHAR(5) | 否 | 'D2' | D2=职业技能 D3=职业素养 D4=发展潜力 |
| proficiency_criteria | JSON | 是 | NULL | 精通/熟练/了解/不会 标准 |
| description | TEXT | 是 | NULL | 描述 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `skill_name` (skill_name)
- INDEX `idx_category` (category)
- INDEX `idx_tier` (tier)
- INDEX `idx_heat` (heat_score DESC)

```sql
CREATE TABLE skill_taxonomy (
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
```

---

### 4.3 learning_path_template(学习路径模板)

**用途**: 冷启动学习路径模板

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| path_id | VARCHAR(50) | 否 | - | 路径唯一 ID |
| target_role | VARCHAR(50) | 否 | - | 目标岗位 |
| target_level | VARCHAR(20) | 否 | '初级' | 目标级别 |
| steps | JSON | 否 | - | [{order, skill, weeks, resources}] |
| total_weeks | INT | 否 | 0 | 总周数 |
| description | TEXT | 是 | NULL | 描述 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `path_id` (path_id)
- INDEX `idx_role` (target_role)

```sql
CREATE TABLE learning_path_template (
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
```

---

### 4.4 student_self_assessment(技能自评)

**用途**: 冷启动到画像的桥梁,学生自评数据

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| assessment_id | VARCHAR(64) | 否 | - | 评估唯一 ID |
| student_id | VARCHAR(64) | 否 | - | 学生 ID |
| assessed_skills | JSON | 否 | - | [{skill, level, confidence}] |
| assessed_abilities | JSON | 是 | NULL | 能力评估 |
| weak_skills | JSON | 是 | NULL | 弱项技能 |
| recommended_roles | JSON | 是 | NULL | 推荐岗位 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |

**索引**:
- UNIQUE `assessment_id` (assessment_id)
- INDEX `idx_student` (student_id)

```sql
CREATE TABLE student_self_assessment (
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
```

---

## 五、实体归一化表

### 5.1 canonical_entity(规范实体库)

**用途**: 归一化规范实体库,支持 R-04/R-03 规则

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| entity_type | VARCHAR(32) | 否 | - | skill/position/major/certificate/degree/award |
| canonical_name | VARCHAR(128) | 否 | - | 规范名称 |
| category | VARCHAR(64) | 是 | NULL | 分类 |
| aliases_json | JSON | 是 | NULL | 别名列表 |
| frequency | INT | 是 | 1 | 出现频次 |
| source | VARCHAR(32) | 是 | 'seed' | seed/oov_auto/oov_review |
| created_at | DATETIME | 是 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 是 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `uk_type_name` (entity_type, canonical_name)
- INDEX `idx_type_category` (entity_type, category)
- INDEX `idx_frequency` (frequency)

```sql
CREATE TABLE canonical_entity (
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
```

---

### 5.2 normalization_rule(归一化外部规则表)

**用途**: 外部归一化规则,支持多种匹配模式

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| rule_domain | VARCHAR(64) | 否 | - | 规则域: degree/certificate_alias/award_level/major_alias/skill_alias |
| match_mode | VARCHAR(32) | 否 | 'contains' | exact/contains/compact_exact/compact_contains |
| pattern | VARCHAR(255) | 否 | - | 触发模式 |
| canonical_value | VARCHAR(255) | 否 | - | 归一化结果 |
| priority | INT | 否 | 100 | 优先级(大优先) |
| enabled | TINYINT(1) | 否 | 1 | 是否启用 |
| source | VARCHAR(32) | 是 | 'seed' | seed/admin/agent_auto |
| created_at | DATETIME | 是 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 是 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `uk_domain_pattern` (rule_domain, pattern)
- INDEX `idx_domain_priority` (rule_domain, priority)
- INDEX `idx_enabled` (enabled)

```sql
CREATE TABLE normalization_rule (
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
```

---

## 六、账号与权限表

### 6.1 user_account(统一账号表)

**用途**: 统一身份层(登录/鉴权/角色),学生场景下通过 student_id 关联 student_profile
**角色体系**: student / teacher / admin(三角色)

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | VARCHAR(64) | 否 | - | 用户唯一 ID(学生场景下与 student_profile.student_id 对齐) |
| username | VARCHAR(50) | 否 | - | 登录用户名(唯一) |
| password_hash | VARCHAR(255) | 否 | - | bcrypt 哈希 |
| name | VARCHAR(100) | 是 | NULL | 姓名 |
| email | VARCHAR(100) | 是 | NULL | 邮箱 |
| phone | VARCHAR(20) | 是 | NULL | 电话(脱敏存储) |
| avatar | VARCHAR(500) | 是 | NULL | 头像 URL |
| role | VARCHAR(20) | 否 | 'student' | student/teacher/admin |
| status | VARCHAR(20) | 否 | 'active' | active/disabled |
| class_id | VARCHAR(64) | 是 | NULL | → class_info.id(学生所属班级) |
| signature | VARCHAR(255) | 是 | NULL | 个性签名 |
| last_login_at | DATETIME | 是 | NULL | 最近登录时间 |
| last_login_ip | VARCHAR(45) | 是 | NULL | 最近登录 IP |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- INDEX `idx_role` (role)
- INDEX `idx_status` (status)
- INDEX `idx_class` (class_id)
- INDEX `idx_created` (created_at)

```sql
CREATE TABLE user_account (
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
```

---

### 6.2 class_info(班级信息)

**用途**: 班级信息管理

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | VARCHAR(64) | 否 | - | 班级唯一 ID |
| name | VARCHAR(100) | 否 | - | 班级名称 |
| teacher_id | VARCHAR(64) | 是 | NULL | → user_account.id(班主任) |
| course_ids | JSON | 是 | NULL | 关联课程 ID 列表 |
| student_count | INT | 否 | 0 | 学生数(冗余字段,定期同步) |
| description | VARCHAR(255) | 是 | NULL | 描述 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- INDEX `idx_teacher` (teacher_id)
- INDEX `idx_name` (name)

```sql
CREATE TABLE class_info (
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
```

---

### 6.3 student_grade(学生成绩)

**用途**: 学生课程成绩管理

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| user_id | VARCHAR(64) | 否 | - | → user_account.id |
| course_id | VARCHAR(64) | 否 | - | → course.course_id |
| total_score | FLOAT | 否 | 0 | 总分 0-100 |
| quiz_scores | JSON | 是 | NULL | 各次小测验分数 [88, 92, 75] |
| completion_rate | FLOAT | 否 | 0 | 完成率 0-1 |
| graded_at | DATETIME | 是 | NULL | 评分时间 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `uk_user_course` (user_id, course_id)
- INDEX `idx_course` (course_id)
- INDEX `idx_score` (total_score DESC)

```sql
CREATE TABLE student_grade (
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
```

---

### 6.4 active_user_log(用户登录日志)

**用途**: DAU/WAU/MAU 活跃用户统计来源

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| user_id | VARCHAR(64) | 否 | - | → user_account.id |
| login_at | DATETIME | 否 | CURRENT_TIMESTAMP | 登录时间 |
| ip | VARCHAR(45) | 是 | NULL | IP 地址 |
| user_agent | VARCHAR(255) | 是 | NULL | User-Agent |

**索引**:
- INDEX `idx_user` (user_id)
- INDEX `idx_login` (login_at)

```sql
CREATE TABLE active_user_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL COMMENT '→ user_account.id',
    login_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ip              VARCHAR(45)  DEFAULT NULL,
    user_agent      VARCHAR(255) DEFAULT NULL,
    INDEX idx_user (user_id),
    INDEX idx_login (login_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户登录日志 (DAU/WAU/MAU 统计来源)';
```

---

### 6.5 admin_audit_log(管理员操作审计日志)

**用途**: 所有 admin 写操作都记录到此表,确保管理员操作可追溯

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| admin_id | VARCHAR(64) | 否 | - | → user_account.id |
| action | VARCHAR(50) | 否 | - | create/update/delete/upload/approve/reject/login |
| target_type | VARCHAR(50) | 否 | - | knowledge_base/dataset/oov/user/config |
| target_id | VARCHAR(64) | 是 | NULL | 目标对象 ID |
| detail_json | JSON | 是 | NULL | 操作详情 |
| ip | VARCHAR(45) | 是 | NULL | IP 地址 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |

**索引**:
- INDEX `idx_admin` (admin_id)
- INDEX `idx_action` (action)
- INDEX `idx_target` (target_type, target_id)
- INDEX `idx_created` (created_at DESC)

```sql
CREATE TABLE admin_audit_log (
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
```

---

## 七、管理员端业务表

### 7.1 knowledge_base(知识库上传记录)

**用途**: 管理员上传的知识库记录,支持智能实体解析

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | VARCHAR(64) | 否 | - | 如 kb_xxxxxxxxxxxx |
| title | VARCHAR(200) | 否 | - | 标题 |
| type | VARCHAR(30) | 否 | 'text' | text/skill/jd/file |
| status | VARCHAR(20) | 否 | 'pending' | pending/processing/completed/failed |
| file_path | VARCHAR(500) | 是 | NULL | 上传文件路径 |
| file_size | BIGINT | 是 | NULL | 字节 |
| content_text | LONGTEXT | 是 | NULL | 原始内容文本 |
| parsed_entities | JSON | 是 | NULL | 解析出的实体(skills/roles/relations) |
| intent_json | JSON | 是 | NULL | 意图识别结果 |
| uploaded_by | VARCHAR(64) | 是 | NULL | → user_account.id |
| error_message | TEXT | 是 | NULL | 错误信息 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- INDEX `idx_status` (status)
- INDEX `idx_type` (type)
- INDEX `idx_created` (created_at DESC)

```sql
CREATE TABLE knowledge_base (
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
```

---

### 7.2 job_dataset(岗位数据集上传记录)

**用途**: 管理员上传的岗位数据集记录

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | VARCHAR(64) | 否 | - | 如 ds_xxxxxxxxxxxx |
| title | VARCHAR(200) | 否 | - | 标题 |
| count | INT | 否 | 0 | 岗位数量 |
| category | VARCHAR(50) | 是 | NULL | 数据集分类 |
| status | VARCHAR(20) | 否 | 'pending' | pending/processing/completed/failed |
| file_path | VARCHAR(500) | 是 | NULL | 文件路径 |
| file_size | BIGINT | 是 | NULL | 字节 |
| description | TEXT | 是 | NULL | 描述 |
| uploaded_by | VARCHAR(64) | 是 | NULL | → user_account.id |
| error_message | TEXT | 是 | NULL | 错误信息 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- INDEX `idx_status` (status)
- INDEX `idx_category` (category)
- INDEX `idx_created` (created_at DESC)

```sql
CREATE TABLE job_dataset (
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
```

---

### 7.3 oov_entity(OOV 实体审核队列)

**用途**: 管理员审核归一化系统未能识别的 OOV(Out-of-Vocabulary)实体

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| oov_id | VARCHAR(64) | 否 | - | 如 oov_xxxxxxxxxxxx |
| raw_entity | VARCHAR(200) | 否 | - | 原始未识别实体 |
| entity_type | VARCHAR(32) | 否 | - | skill/position/major/certificate/degree/award |
| context_json | JSON | 是 | NULL | 上下文(出现位置/句子) |
| source | VARCHAR(64) | 是 | NULL | 来源: 简历 ID/JD ID/知识库 ID |
| frequency | INT | 否 | 1 | 出现频次 |
| status | VARCHAR(20) | 否 | 'pending' | pending/approved/rejected |
| canonical_mapping | VARCHAR(200) | 是 | NULL | → canonical_entity.canonical_name(审核后) |
| reviewed_by | VARCHAR(64) | 是 | NULL | → user_account.id |
| reviewed_at | DATETIME | 是 | NULL | 审核时间 |
| review_notes | TEXT | 是 | NULL | 审核备注(拒绝原因等) |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |

**索引**:
- UNIQUE `oov_id` (oov_id)
- INDEX `idx_status` (status)
- INDEX `idx_type` (entity_type)
- INDEX `idx_source` (source)
- INDEX `idx_created` (created_at DESC)

```sql
CREATE TABLE oov_entity (
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
```

---

## 八、API 扩展表

### 8.1 saved_job(收藏岗位)

**用途**: 用户收藏的岗位信息

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| user_id | VARCHAR(64) | 否 | - | 用户 ID |
| job_id | VARCHAR(64) | 否 | - | 岗位 ID |
| job_title | VARCHAR(200) | 是 | NULL | 岗位名称 |
| company | VARCHAR(200) | 是 | NULL | 公司名称 |
| industry | VARCHAR(100) | 是 | NULL | 行业 |
| salary | VARCHAR(100) | 是 | NULL | 薪资范围 |
| location | VARCHAR(200) | 是 | NULL | 工作地点 |
| match_score | FLOAT | 是 | NULL | 匹配分数 |
| required_skills | JSON | 是 | NULL | 必备技能列表 |
| saved_at | DATETIME | 否 | CURRENT_TIMESTAMP | 收藏时间 |

**索引**:
- INDEX `idx_user` (user_id)
- INDEX `idx_job` (job_id)
- INDEX `idx_saved` (saved_at DESC)

```sql
CREATE TABLE saved_job (
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
```

---

### 8.2 target_role(目标岗位)

**用途**: 用户的目标岗位方向

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| user_id | VARCHAR(64) | 否 | - | 用户 ID |
| role_name | VARCHAR(200) | 否 | - | 目标岗位名称 |
| priority | INT | 否 | 1 | 优先级 |
| salary_expectation | VARCHAR(100) | 是 | NULL | 薪资期望 |
| location_preference | VARCHAR(200) | 是 | NULL | 地点偏好 |
| notes | TEXT | 是 | NULL | 备注 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- INDEX `idx_user` (user_id)
- INDEX `idx_priority` (priority DESC)

```sql
CREATE TABLE target_role (
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
```

---

### 8.3 course(课程)

**用途**: 课程体系表

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| course_id | VARCHAR(64) | 否 | - | 课程唯一 ID |
| title | VARCHAR(200) | 否 | - | 课程标题 |
| category | VARCHAR(100) | 是 | NULL | 课程分类 |
| description | TEXT | 是 | NULL | 课程描述 |
| difficulty | VARCHAR(20) | 是 | 'beginner' | 难度: beginner/intermediate/advanced |
| duration_hours | FLOAT | 是 | 0 | 课程时长(小时) |
| instructor | VARCHAR(100) | 是 | NULL | 讲师 |
| thumbnail_url | VARCHAR(500) | 是 | NULL | 缩略图 URL |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**:
- UNIQUE `course_id` (course_id)
- INDEX `idx_category` (category)
- INDEX `idx_difficulty` (difficulty)

```sql
CREATE TABLE course (
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
```

---

### 8.4 chapter(课程章节)

**用途**: 课程章节表,与 learning_progress 关联使用

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| course_id | VARCHAR(64) | 否 | - | 课程 ID |
| title | VARCHAR(200) | 否 | - | 章节标题 |
| sequence | INT | 否 | 0 | 章节序号 |
| duration_hours | FLOAT | 是 | 0 | 章节时长(小时) |
| content_summary | TEXT | 是 | NULL | 内容摘要 |
| created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |

**索引**:
- INDEX `idx_course` (course_id)
- INDEX `idx_sequence` (sequence)

```sql
CREATE TABLE chapter (
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
```

---

### 8.5 learning_progress(学习进度)

**用途**: 用户学习进度跟踪

| 字段名 | 类型 | 可空 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 否 | AUTO_INCREMENT | 主键 |
| user_id | VARCHAR(64) | 否 | - | 用户 ID |
| course_id | VARCHAR(64) | 否 | - | 课程 ID |
| chapter_id | VARCHAR(64) | 否 | - | 章节 ID |
| progress | FLOAT | 否 | 0 | 进度 0-100 |
| completed | TINYINT(1) | 否 | 0 | 是否完成 |
| last_position | INT | 是 | 0 | 上次播放位置(秒) |
| updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |
| completed_at | DATETIME | 是 | NULL | 完成时间 |

**索引**:
- UNIQUE `uk_user_chapter` (user_id, chapter_id)
- INDEX `idx_user_course` (user_id, course_id)

```sql
CREATE TABLE learning_progress (
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
```

---

## 九、表关系说明

> 以下用文字描述表之间的关联关系。

### 9.1 核心业务链(L0-L4 管线)

```
student_profile (L0 学生简历)
  ├── 1:N → student_portrait_4d (L2 学生画像, 按 target_job_id 区分)
  │         ├── 1:1 → match_result (L3 匹配结果)
  │         ├── 1:1 → skill_gap (L3 技能差距)
  │         └── 1:1 → career_path (L3 职业路径)
  └── 1:N → career_report (L4 职业报告)

job_posting (L0 岗位信息)
  └── N:1 → job_portrait_4d (L2 岗位画像, 多个 JD 聚合成一个画像)
            ↑
            job_portrait_4d.portrait_id 被 match_result / skill_gap / career_path 引用
```

**关联字段**:
- `student_portrait_4d.student_id` → `student_profile.student_id`
- `match_result.student_id` → `student_profile.student_id`
- `match_result.job_portrait_id` → `job_portrait_4d.portrait_id`
- `career_report.student_id` → `student_profile.student_id`
- `career_report.job_portrait_id` → `job_portrait_4d.portrait_id`

### 9.2 会话链

```
student_profile
  └── 1:N → conversation (对话会话)
            └── 1:N → conversation_message (对话消息)
```

**关联字段**:
- `conversation.student_id` → `student_profile.student_id`
- `conversation_message.session_id` → `conversation.session_id`

### 9.3 账号与权限链

```
user_account (统一账号)
  ├── N:1 → class_info (班级, 学生所属)
  │         └── N:1 → user_account (班主任, 反向关联)
  ├── 1:N → student_grade (学生成绩)
  ├── 1:N → active_user_log (登录日志)
  ├── 1:N → admin_audit_log (管理员审计, admin 角色)
  └── 1:1 → student_profile (学生场景下, user_account.id = student_profile.student_id)
```

**关联字段**:
- `user_account.class_id` → `class_info.id`
- `class_info.teacher_id` → `user_account.id`
- `student_grade.user_id` → `user_account.id`
- `active_user_log.user_id` → `user_account.id`
- `admin_audit_log.admin_id` → `user_account.id`

### 9.4 管理员端业务链

```
user_account (admin 角色)
  └── 1:N → knowledge_base (知识库上传, uploaded_by)
  └── 1:N → job_dataset (数据集上传, uploaded_by)
  └── 1:N → oov_entity (OOV 审核, reviewed_by)
            └── approved → canonical_entity (审核通过后入规范库)
```

**关联字段**:
- `knowledge_base.uploaded_by` → `user_account.id`
- `job_dataset.uploaded_by` → `user_account.id`
- `oov_entity.reviewed_by` → `user_account.id`
- `oov_entity.canonical_mapping` → `canonical_entity.canonical_name`(逻辑外键)

### 9.5 学习扩展链

```
user_account
  ├── 1:N → saved_job (收藏岗位)
  ├── 1:N → target_role (目标岗位)
  └── 1:N → learning_progress (学习进度)
            ├── N:1 → chapter (章节)
            │        └── N:1 → course (课程)
            └── N:1 → course (课程)

course
  └── 1:N → chapter
            └── 1:N → learning_progress

student_grade
  ├── N:1 → user_account
  └── N:1 → course
```

**关联字段**:
- `saved_job.user_id` → `user_account.id`
- `saved_job.job_id` → `job_posting.job_id`(逻辑外键)
- `target_role.user_id` → `user_account.id`
- `chapter.course_id` → `course.course_id`
- `learning_progress.user_id` → `user_account.id`
- `learning_progress.course_id` → `course.course_id`
- `learning_progress.chapter_id` → `chapter.id`(逻辑外键)
- `student_grade.user_id` → `user_account.id`
- `student_grade.course_id` → `course.course_id`

### 9.6 独立表

以下表通过 `student_id` / `job_id` 弱关联,不参与强外键约束:

- `pipeline_task`: 异步任务追踪,通过 `student_id` / `job_id` 弱关联
- `student_self_assessment`: 学生自评,通过 `student_id` 关联
- `career_insights`: 职业方向摘要,独立冷启动数据
- `skill_taxonomy`: 技能分类树,独立冷启动数据
- `learning_path_template`: 学习路径模板,独立冷启动数据
- `canonical_entity`: 规范实体库,被 `oov_entity` 逻辑引用
- `normalization_rule`: 归一化规则,独立配置数据

---

## 十、四维画像(D1-D4)说明

系统核心采用四维画像模型,贯穿学生画像、岗位画像、匹配结果全链路:

| 维度 | 名称 | 权重 | 含义 |
|---|---|---|---|
| D1 | 基础要求 | 0.25 | 学历、专业、学校、GPA 等硬性条件 |
| D2 | 职业技能 | 0.35 | 岗位要求的技术技能覆盖率和熟练度 |
| D3 | 职业素养 | 0.20 | 软技能、团队协作、沟通能力(Shannon 熵映射置信度) |
| D4 | 发展潜力 | 0.20 | 项目经验、竞赛获奖、科研成果、学习能力 |

**综合得分公式**:
```
competitiveness = D1 × 0.25 + D2 × 0.35 + D3 × 0.20 + D4 × 0.20
```

**竞争力等级**:
- 90-100: 极强
- 75-89: 强
- 60-74: 中等
- 40-59: 偏弱
- 0-39: 弱

---

## 十一、ORM 模型对应关系

| 数据库表 | SQLAlchemy ORM 模型 | 模型文件 |
|---|---|---|
| student_profile | StudentProfile | backend/app/models/student.py |
| student_portrait_4d | StudentPortrait4D | backend/app/models/student.py |
| job_posting | JobPosting | backend/app/models/job.py |
| job_portrait_4d | JobPortrait4D | backend/app/models/job.py |
| match_result | MatchResult | backend/app/models/match.py |
| skill_gap | SkillGap | backend/app/models/match.py |
| career_path | CareerPath | backend/app/models/match.py |
| career_report | CareerReport | backend/app/models/report.py |
| conversation | Conversation | backend/app/models/conversation.py |
| conversation_message | ConversationMessage | backend/app/models/conversation.py |
| pipeline_task | PipelineTask | backend/app/models/task.py |
| user_account | UserAccount | backend/app/models/account.py |
| class_info | ClassInfo | backend/app/models/account.py |
| student_grade | StudentGrade | backend/app/models/account.py |
| active_user_log | ActiveUserLog | backend/app/models/account.py |
| admin_audit_log | AdminAuditLog | backend/app/models/account.py |
| knowledge_base | KnowledgeBase | backend/app/models/admin_data.py |
| job_dataset | JobDataset | backend/app/models/admin_data.py |
| oov_entity | OovEntity | backend/app/models/admin_data.py |

---

**文档结束**
