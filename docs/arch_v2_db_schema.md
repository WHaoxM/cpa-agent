# CPA-Agent 存储 Schema 设计

> **设计日期**: 2026-04-05  
> **状态**: 软冻结 (允许加列/加索引, 不允许改已有列/删列)  
> **前提**: 基于已冻结的 JSON 输出 Schema, 设计底层存储结构  
> **设计原则**: 核心字段拆列建索引 + 完整 JSON 列保留原始结构 (不过度范式化)

---

## 一、MySQL 8.0 — 全量业务主库

### 设计策略

**Hybrid 模式**: 高频查询/JOIN 字段 → 独立列 + 索引; 完整结构化数据 → JSON 列

理由:
- 冻结的 JSON Schema 嵌套深 (如 D1_basic.sub_degree), 完全范式化会产生 20+ 表, 竞赛不值得
- MySQL 8.0 原生支持 JSON 列 + 虚拟生成列索引, 可以 JSON_EXTRACT 查询
- 核心字段拆列保证 JOIN 性能和可读性

### 1.1 student_profile (学生简历 — L0 输出)

```sql
CREATE TABLE student_profile (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id      VARCHAR(64)  NOT NULL UNIQUE COMMENT '学生唯一ID',
    name            VARCHAR(100) DEFAULT NULL COMMENT '姓名',
    phone           VARCHAR(20)  DEFAULT NULL COMMENT '电话 (脱敏存储)',
    email           VARCHAR(100) DEFAULT NULL COMMENT '邮箱',
    
    -- 关键可查询字段 (从 JSON 拆出)
    degree          VARCHAR(20)  DEFAULT NULL COMMENT '最高学历: 本科/硕士/博士',
    school          VARCHAR(100) DEFAULT NULL COMMENT '毕业院校',
    major           VARCHAR(100) DEFAULT NULL COMMENT '专业',
    graduation_year SMALLINT     DEFAULT NULL COMMENT '毕业年份',
    
    -- 完整 L0 解析结果 (冻结 Schema 的 16 字段全量)
    profile_json    JSON         NOT NULL     COMMENT '完整 student_profile.json',
    
    -- 数据质量
    data_quality    JSON         DEFAULT NULL COMMENT 'L0 解析质量指标',
    completeness    FLOAT        DEFAULT NULL COMMENT '字段填充率 0-1',
    
    -- 来源追踪
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

### 1.2 job_posting (岗位信息 — L0 输出)

```sql
CREATE TABLE job_posting (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_id              VARCHAR(64)  NOT NULL UNIQUE COMMENT '岗位唯一ID',
    position_name       VARCHAR(200) NOT NULL        COMMENT '岗位名称 (原始)',
    position_normalized VARCHAR(200) DEFAULT NULL     COMMENT '标准化岗位名',
    job_category        VARCHAR(50)  DEFAULT NULL     COMMENT '岗位大类',
    
    -- 公司信息 (拆出高频查询字段)
    company_name        VARCHAR(200) DEFAULT NULL COMMENT '公司名',
    company_industry    VARCHAR(100) DEFAULT NULL COMMENT '行业',
    company_size        VARCHAR(50)  DEFAULT NULL COMMENT '规模',
    
    -- 核心要求 (拆出用于匹配)
    degree_requirement  VARCHAR(20)  DEFAULT NULL COMMENT '学历要求',
    experience_min      SMALLINT     DEFAULT NULL COMMENT '最低经验(年)',
    salary_min          INT          DEFAULT NULL COMMENT '最低薪资(元/月)',
    salary_max          INT          DEFAULT NULL COMMENT '最高薪资(元/月)',
    
    -- 完整 L0 解析结果
    posting_json        JSON         NOT NULL     COMMENT '完整 job_posting.json',
    
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
```

### 1.3 student_portrait_4d (学生四维画像 — L2 输出)

```sql
CREATE TABLE student_portrait_4d (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    target_job_id       VARCHAR(64)  DEFAULT NULL COMMENT '目标岗位ID (D2 覆盖率相对)',
    
    -- 四维得分 (拆出用于排序/筛选)
    d1_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D1 基础要求得分 0-100',
    d2_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D2 职业技能得分 0-100',
    d3_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D3 职业素养得分 0-100',
    d4_score            FLOAT        NOT NULL DEFAULT 0 COMMENT 'D4 发展潜力得分 0-100',
    
    -- 综合得分
    competitiveness     FLOAT        NOT NULL DEFAULT 0 COMMENT 'D1×0.25+D2×0.35+D3×0.20+D4×0.20',
    competitiveness_lvl VARCHAR(10)  DEFAULT NULL       COMMENT '极强/强/中等/偏弱/弱',
    completeness        FLOAT        NOT NULL DEFAULT 0 COMMENT '字段填充率 0-100',
    
    -- D2 关键指标
    skill_coverage      FLOAT        DEFAULT NULL COMMENT '技能覆盖率 0-1 (R-03 ≥0.8)',
    skill_count         INT          DEFAULT 0    COMMENT '技能总数',
    
    -- D3 置信度
    d3_confidence       FLOAT        DEFAULT NULL COMMENT 'D3 Shannon 熵映射 0-1',
    
    -- 完整画像 JSON (冻结 Schema)
    portrait_json       JSON         NOT NULL     COMMENT '完整 student_portrait_4d.json',
    
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_student_job (student_id, target_job_id),
    INDEX idx_competitiveness (competitiveness DESC),
    INDEX idx_level (competitiveness_lvl),
    INDEX idx_skill_coverage (skill_coverage)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生四维画像 (L2 输出)';
```

### 1.4 job_portrait_4d (岗位四维需求画像 — L2 输出)

```sql
CREATE TABLE job_portrait_4d (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    portrait_id         VARCHAR(64)  NOT NULL UNIQUE COMMENT '画像唯一ID (聚合后)',
    position_name       VARCHAR(200) NOT NULL        COMMENT '标准化岗位名',
    
    -- 聚合统计
    jd_count            INT          NOT NULL DEFAULT 0 COMMENT '聚合的 JD 数量 (R-05 ≥10)',
    
    -- 核心需求 (拆出)
    required_degree     VARCHAR(20)  DEFAULT NULL COMMENT '学历要求',
    major_strict        TINYINT(1)   DEFAULT 0    COMMENT '是否严格限专业',
    required_skill_cnt  INT          DEFAULT 0    COMMENT '必备技能数',
    
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
```

### 1.5 match_result (匹配结果 — L3 输出)

```sql
CREATE TABLE match_result (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64) NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64) NOT NULL COMMENT '→ job_portrait_4d.portrait_id',
    
    -- 四维匹配得分
    d1_match            FLOAT       NOT NULL DEFAULT 0 COMMENT 'D1 匹配得分',
    d2_match            FLOAT       NOT NULL DEFAULT 0 COMMENT 'D2 匹配得分',
    d3_match            FLOAT       NOT NULL DEFAULT 0 COMMENT 'D3 匹配得分',
    d4_match            FLOAT       NOT NULL DEFAULT 0 COMMENT 'D4 匹配得分',
    total_score         FLOAT       NOT NULL DEFAULT 0 COMMENT '加权总分',
    
    -- 匹配判定
    d1_pass             TINYINT(1)  NOT NULL DEFAULT 0 COMMENT 'D1 硬约束是否通过',
    skill_coverage      FLOAT       DEFAULT NULL       COMMENT 'D2 技能覆盖率',
    is_matched          TINYINT(1)  NOT NULL DEFAULT 0 COMMENT '最终是否匹配',
    match_level         VARCHAR(20) DEFAULT NULL        COMMENT '极佳/良好/一般/不匹配',
    
    -- 完整结果 JSON
    result_json         JSON        NOT NULL COMMENT '完整 match_result.json',
    
    created_at          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_total_score (total_score DESC),
    INDEX idx_is_matched (is_matched),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人岗匹配结果 (L3 输出)';
```

### 1.6 skill_gap (技能差距 — L3 输出)

```sql
CREATE TABLE skill_gap (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64) NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64) NOT NULL COMMENT '→ job_portrait_4d.portrait_id',
    
    -- 差距统计
    critical_count      INT         NOT NULL DEFAULT 0 COMMENT '关键缺失技能数',
    quick_learn_count   INT         NOT NULL DEFAULT 0 COMMENT '快速可学技能数',
    total_gap_count     INT         NOT NULL DEFAULT 0 COMMENT '总差距技能数',
    
    -- 完整结果 JSON
    gap_json            JSON        NOT NULL COMMENT '完整 skill_gap.json',
    
    created_at          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技能差距分析 (L3 输出)';
```

### 1.7 career_path (职业路径推荐 — L3 输出)

```sql
CREATE TABLE career_path (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id          VARCHAR(64)  NOT NULL COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64)  NOT NULL COMMENT '→ job_portrait_4d.portrait_id',
    
    -- 推荐结果摘要
    recommended_dir     VARCHAR(200) DEFAULT NULL COMMENT '推荐发展方向',
    promotion_steps     INT          DEFAULT 0    COMMENT '晋升路径步数',
    lateral_count       INT          DEFAULT 0    COMMENT '横向选项数',
    
    -- 完整结果 JSON
    path_json           JSON         NOT NULL COMMENT '完整 career_path.json',
    
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_student_job (student_id, job_portrait_id),
    INDEX idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='职业路径推荐 (L3 输出)';
```

### 1.8 career_report (职业发展报告 — L4 输出)

```sql
CREATE TABLE career_report (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    report_id           VARCHAR(64)  NOT NULL UNIQUE COMMENT '报告唯一ID',
    student_id          VARCHAR(64)  NOT NULL        COMMENT '→ student_profile.student_id',
    job_portrait_id     VARCHAR(64)  NOT NULL        COMMENT '→ job_portrait_4d.portrait_id',
    
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
```

### 1.9 conversation (对话会话)

```sql
CREATE TABLE conversation (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL UNIQUE COMMENT '会话唯一ID',
    student_id      VARCHAR(64)  DEFAULT NULL    COMMENT '关联学生ID (可选)',
    
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
```

### 1.10 conversation_message (对话消息)

```sql
CREATE TABLE conversation_message (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL        COMMENT '→ conversation.session_id',
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
```

### 1.11 pipeline_task (管线任务)

```sql
CREATE TABLE pipeline_task (
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
```

### 表关系图

```
student_profile ──1:N──→ student_portrait_4d ──1:1──→ match_result
       │                       │                          │
       │                       │                    skill_gap
       │                       │                          │
       │                       └──1:1──→ career_path
       │
       └──1:N──→ career_report
       │
       └──1:N──→ conversation ──1:N──→ conversation_message

job_posting ──N:1──→ job_portrait_4d

pipeline_task (独立, 通过 student_id/job_id 弱关联)
```

---

## 二、Neo4j — 知识图谱

### 2.1 Graph A: 技能与职业知识图谱 (L1 构建)

#### 节点 Labels

| Label | 属性 | 说明 | 数量级 |
|-------|------|------|--------|
| **Skill** | name, aliases[], category, sub_category, frequency, proficiency_levels[], embedding[1024], created_at | 技能节点 | ~500 |
| **SkillCategory** | name, level(major/sub), parent_name, skill_count | 技能分类 | 6 大类 + 24 子类 |
| **JobPortrait** | portrait_id, position_name, jd_count, required_degree, d1_weight, d2_weight, d3_weight, d4_weight | 岗位聚合画像 | ≥10 |
| **CareerNode** | position_name, level(junior/mid/senior/lead/director), family | 职业路径节点 | ~30-50 |

#### 关系 Types

| Type | 起点→终点 | 属性 | 说明 |
|------|-----------|------|------|
| **BELONGS_TO** | Skill → SkillCategory | — | 技能归属分类 |
| **CHILD_OF** | SkillCategory → SkillCategory | — | 子类→大类 |
| **REQUIRES** | JobPortrait → Skill | level, tf_score | 必备技能 |
| **PREFERS** | JobPortrait → Skill | tf_score | 优选技能 |
| **BONUS** | JobPortrait → Skill | tf_score | 加分技能 |
| **PROMOTION** | CareerNode → CareerNode | years, skill_delta | 晋升边 (≥3链, R-06) |
| **TRANSFER** | CareerNode → CareerNode | jaccard, affinity, skill_overlap | 换岗边 (≥5×2, R-07) |
| **PREREQUISITE** | Skill → Skill | confidence | 前置技能 (v3.0) |
| **REPLACES** | Skill → Skill | confidence, since_year | 替代关系 (v3.0) |
| **EVOLVES_TO** | Skill → Skill | confidence | 演进关系 (v3.0) |

#### 索引

```cypher
-- 唯一约束
CREATE CONSTRAINT skill_name IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT portrait_id IF NOT EXISTS FOR (j:JobPortrait) REQUIRE j.portrait_id IS UNIQUE;
CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:SkillCategory) REQUIRE c.name IS UNIQUE;

-- 查询索引
CREATE INDEX skill_category IF NOT EXISTS FOR (s:Skill) ON (s.category);
CREATE INDEX career_family IF NOT EXISTS FOR (c:CareerNode) ON (c.family);
CREATE INDEX career_level IF NOT EXISTS FOR (c:CareerNode) ON (c.level);

-- 全文索引 (支持 Text-to-Cypher 模糊查询)
CREATE FULLTEXT INDEX skill_fulltext IF NOT EXISTS FOR (s:Skill) ON EACH [s.name, s.aliases];
```

### 2.2 Graph B: 简历实体关系网络 (L0 抽取)

基于冻结的 19 种实体 + 24 种关系, 每份简历的实体关系图入 Neo4j。

#### 节点 Labels (19 种, 与冻结 Schema 对齐)

```
Person, School, College, Major, Degree, Company, Position,
Project, Institution, Skill, Tool, Certificate, Award,
Competition, Language, DateRange, Location, Organization, SoftSkill
```

**通用属性**: `entity_id, name, student_id, source(resume/jd), confidence, created_at`

#### 关系 Types (24 种, 与冻结 Schema 对齐)

```
STUDIED_AT, HAS_MAJOR, HAS_DEGREE, COLLEGE_OF,
HAS_CERTIFICATE, HAS_LANGUAGE,                     -- D1
HAS_SKILL, USES_TOOL, WORKED_AT, INTERNED_AT,
HAS_TITLE, PROJECT_USES, INDUSTRY_OF,              -- D2
DEMONSTRATES, MEMBER_OF,                            -- D3
WON_AWARD, COMPETED_IN, RESEARCHED_AT,
PUBLISHED, PARTICIPATED_IN,                         -- D4
HAS_PERIOD, LOCATED_IN, PROJECT_AT                  -- 辅助
```

**通用属性**: `relation_id, matching_dimension(D1/D2/D3/D4/null), confidence, evidence, student_id`

#### 索引 (Graph B)

```cypher
-- 按 student_id 查询该学生的所有实体
CREATE INDEX person_student IF NOT EXISTS FOR (p:Person) ON (p.student_id);
-- 按 entity name 查询
CREATE INDEX entity_name IF NOT EXISTS FOR (n:Skill) ON (n.name);
```

---

## 三、Milvus Lite — 向量存储

### 设计策略

- **嵌入式部署**: `pymilvus[lite]`, 数据文件 `./milvus_data/milvus.db`
- **Embedding**: BGE-M3, dim=1024
- **距离度量**: COSINE (归一化向量)
- **索引类型**: IVF_FLAT (竞赛数据量 <10k, 不需要 HNSW)

### 3.1 skill_vectors (技能向量)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT64 (PK) | 自增主键 |
| skill_name | VARCHAR(200) | 技能名称 |
| category | VARCHAR(100) | 所属分类 |
| embedding | FLOAT_VECTOR(1024) | BGE-M3 向量 |

**用途**: 语义匹配 (cosine > 0.85 = semantic match), OOV 归类, 相似技能推荐

### 3.2 jd_vectors (JD 描述向量)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT64 (PK) | 自增主键 |
| job_id | VARCHAR(64) | 岗位ID |
| position_name | VARCHAR(200) | 岗位名称 |
| text_chunk | VARCHAR(2000) | JD 文本片段 |
| embedding | FLOAT_VECTOR(1024) | BGE-M3 向量 |

**用途**: "跟我简历最匹配的岗位" 语义检索

### 3.3 resume_vectors (简历向量)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT64 (PK) | 自增主键 |
| student_id | VARCHAR(64) | 学生ID |
| section_type | VARCHAR(50) | education/work/project/skill/... |
| text_chunk | VARCHAR(2000) | 简历文本片段 |
| embedding | FLOAT_VECTOR(1024) | BGE-M3 向量 |

**用途**: 简历语义检索, Agent RAG 上下文补充

### 3.4 conversation_vectors (对话记忆向量)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT64 (PK) | 自增主键 |
| session_id | VARCHAR(64) | 会话ID |
| student_id | VARCHAR(64) | 学生ID (允许跨会话检索) |
| role | VARCHAR(20) | user/assistant |
| content | VARCHAR(2000) | 消息内容 |
| timestamp | INT64 | Unix 时间戳 |
| embedding | FLOAT_VECTOR(1024) | BGE-M3 向量 |

**用途**: 跨会话 RAG 语义检索 ("之前我们聊过Python相关的内容吗")

---

## 四、Redis 7.x — 会话缓存

### Key 命名规范

```
{namespace}:{resource_type}:{resource_id}[:{sub_key}]
```

### 4.1 会话相关

| Key Pattern | 类型 | TTL | 说明 |
|-------------|------|-----|------|
| `session:{session_id}` | HASH | 2h (活跃续期) | 当前会话上下文 |
| `session:{session_id}:history` | LIST | 2h | 最近 N 轮对话 (滑动窗口) |
| `session:{session_id}:student` | STRING | 2h | 关联的 student_id |

**session HASH 字段**:
```
student_id      → "stu_001"
student_name    → "张三"
current_job_id  → "job_java_backend"  (当前讨论的目标岗位)
portrait_summary → "{d1:72, d2:65, d3:58, d4:80, total:67.3}"  (画像摘要)
message_count   → "12"
last_active     → "2026-04-05T19:30:00"
```

**session:history LIST**: 存最近 20 条消息的 JSON 字符串, LPUSH + LTRIM 实现滑动窗口

### 4.2 缓存相关

| Key Pattern | 类型 | TTL | 说明 |
|-------------|------|-----|------|
| `cache:portrait:job:{portrait_id}` | STRING | 1h | 岗位画像 JSON 缓存 |
| `cache:match:{student_id}:{portrait_id}` | STRING | 30min | 匹配结果缓存 |
| `cache:taxonomy:categories` | STRING | 24h | 技能分类树缓存 |
| `cache:graph:stats` | STRING | 1h | 图谱统计信息缓存 |

### 4.3 任务相关

| Key Pattern | 类型 | TTL | 说明 |
|-------------|------|-----|------|
| `task:{task_id}` | HASH | 1h | 管线任务实时状态 |
| `task:{task_id}:progress` | STRING | 1h | 进度百分比 (SSE 推送用) |

**task HASH 字段**:
```
pipeline   → "L0_parse_resume"
status     → "running"
progress   → "45"
message    → "正在抽取实体..."
started_at → "2026-04-05T19:30:00"
```

---

## 五、数据流向总览

```
┌──────────────────────────────────────────────────────────────┐
│                       数据写入路径                            │
│                                                              │
│  简历上传 ─→ L0 解析 ─→ MySQL (student_profile)             │
│                │                                             │
│                ├─→ Neo4j Graph B (19实体+24关系)             │
│                └─→ Milvus (resume_vectors)                   │
│                                                              │
│  JD 导入 ──→ L0 解析 ─→ MySQL (job_posting)                 │
│                └─→ Milvus (jd_vectors)                       │
│                                                              │
│  L1 构建 ──→ Neo4j Graph A (Skill+JobPortrait+Career)       │
│                └─→ Milvus (skill_vectors)                    │
│                                                              │
│  L2 画像 ──→ MySQL (student_portrait_4d / job_portrait_4d)  │
│  L3 匹配 ──→ MySQL (match_result / skill_gap / career_path) │
│  L4 报告 ──→ MySQL (career_report)                          │
│                                                              │
│  Agent 对话 → MySQL (conversation + message)                 │
│                └─→ Milvus (conversation_vectors)             │
│                └─→ Redis (session + history)                 │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                       数据查询路径                            │
│                                                              │
│  Agent 对话 ─→ Redis (session 上下文)                        │
│       │                                                      │
│       ├─ Tool 1 graph_query ─→ Neo4j (Cypher 查询)          │
│       ├─ Tool 2 db_query    ─→ MySQL (结构化查询)           │
│       ├─ Tool 3 semantic    ─→ Milvus (向量检索)            │
│       ├─ Tool 4 pipeline    ─→ Redis (task 状态) + MySQL    │
│       └─ Tool 5 llm_reason  ─→ LLM API                     │
│                                                              │
│  知识库导出 (R-09) ─→ Neo4j + Milvus → JSON                │
└──────────────────────────────────────────────────────────────┘
```

---

## 六、初始化脚本清单

| 脚本 | 功能 | 依赖 |
|------|------|------|
| `scripts/init_mysql.py` | 创建所有 MySQL 表 | MySQL 连接 |
| `scripts/init_neo4j.py` | 创建约束 + 索引 | Neo4j 连接 |
| `scripts/init_milvus.py` | 创建 4 个 Collection | Milvus 连接 |
| `scripts/seed_taxonomy.py` | 导入 skill_taxonomy ~500 节点 → Neo4j + Milvus | JSON 种子数据 |
| `scripts/seed_job_data.py` | 导入 JD 数据 → MySQL + Milvus | Excel/JSON |
