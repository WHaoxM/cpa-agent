# 账号权限分配设计 — 未实施文档

> **状态**: 暂不实施 (Pending)
> **记录日期**: 2026-07-14
> **触发**: 后端功能闭环测试完成后，对账号权限分配设计进行完整性评估
> **决定**: 用户明确指示"暂不实施"，先做数据隔离配置
> **后续触发条件**: 数据隔离改造完成后，或后端准备对外暴露时启动

---

## 一、评估结论

当前 CPA-Agent 项目的账号权限分配设计**几乎不存在**，处于"裸奔"状态。后端 API 完全开放、数据库全用 root、Redis 无密码、真实 API Key 泄露在版本控制中、前端登录是纯 mock 不联动后端。

### 完整性评估总表

| 维度 | 完整性 | 评级 |
|------|--------|------|
| 后端认证机制（JWT/Session/API Key） | 0% | 完全缺失 |
| 后端授权机制（装饰器/RBAC） | 0% | 完全缺失 |
| 数据库账号分离（非 root） | 0% | 完全缺失 |
| Redis 密码 | 0% | 完全缺失 |
| Neo4j 密码 | 50% | 弱密码 |
| Milvus 认证 | N/A | 嵌入式模式 |
| RBAC 数据模型 | 50% | 表结构在，未实现 |
| 前端登录界面 | 70% | UI 完整但是 mock |
| 前端 API 鉴权头 | 0% | 完全缺失 |
| 密钥管理 | 20% | .env 泄露，无 .gitignore |
| 审计日志 | 10% | 表+ORM 在，未写入 |
| CORS 配置 | 20% | 默认 `*`，过宽 |

---

## 二、关键证据

### 2.1 后端认证机制 — 完全缺失

- `backend/app/middleware/` 仅有 `logging_middleware.py`、`error_handler.py`、`cors_middleware.py`，**无 AuthMiddleware**
- 全后端搜索 `login_required|auth_required|@auth|require_auth|admin_required|role_required|check_token|verify_token`，**0 条匹配**
- 无 `/api/auth/login`、`/api/auth/register`、`/api/auth/logout` 等接口
- `models/account.py:25` 和 `init_mysql.sql:485` 注释 `password_hash ... COMMENT 'bcrypt 哈希'`，但**整个后端从未 import 或调用 bcrypt**
- `scripts/fix_user_accounts.py:8-12` 的 password_hash 是 `$2b$12$placeholder_s2` **占位符，非合法 bcrypt 哈希**

### 2.2 越权漏洞（IDOR）

- `favorites.py:22-28`：`user_id` 直接从 `request.args.get('user_id')` 获取，可伪造任意用户
- `learning.py:60-72, 75-109`：学习进度 API 的 `user_id` 同样从请求参数获取
- `profile.py`：输入任意 `student_id` 即可查询任意学生简历

### 2.3 数据库账号 — 全用 root

- `backend/.env:20` → `MYSQL_USER=root`
- `backend/.env.example:23` → `MYSQL_USER=root`（模板默认推荐 root）
- `docker-compose.prod.yml:24` → 生产环境也是 `MYSQL_USER: "root"`
- `init_mysql.sql` 整个脚本中**无 `CREATE USER` 或 `GRANT` 语句**

### 2.4 Redis / Neo4j / Milvus 配置

- `docker-compose.yml:48-50` Redis 启动命令无 `--requirepass`，`REDIS_URL=redis://localhost:6379/0` 无认证
- Neo4j 密码 `neo4jpass`（`docker-compose.yml:32`），`config.py:64` 的 `NEO4J_PASSWORD` 默认值为空字符串
- Milvus 嵌入式模式 `./milvus_data/milvus.db`，无网络认证需求

### 2.5 RBAC 数据模型存在但代码未实现

- `init_mysql.sql:482-502` 有 `user_account` 表（含 role/password_hash/status/last_login_at/last_login_ip）
- `init_mysql.sql:554-567` 有 `admin_audit_log` 表
- `models/account.py:13-52` 的 `UserAccount` ORM 模型已定义
- **但代码层零实现**：无 API 读取 user_account 做登录验证，无装饰器检查 role，admin API 从不写入 admin_audit_log
- `UserAccount.last_login_at` / `last_login_ip` 字段从未被更新

### 2.6 前后端角色定义不一致

- 后端 `init_mysql.sql:481` 注释：`student / teacher / admin`
- 前端 `vue-kecheng/src/types/user.ts:2-5`：仅 `STUDENT = 'student'` 和 `ADMIN = 'admin'`，**无 TEACHER**

### 2.7 前端登录是纯 mock

- `vue-kecheng/src/stores/user.ts:21-34` 的 `login()` 用本地 `mockUsers` 数组匹配，密码硬编码 `'123456'`
- token 是 `mock_token_${user.id}_${Date.now()}`，纯字符串无 JWT 签名
- `LoginView.vue:46-54` 的登录流程**不调用任何后端 API**
- 路由守卫 `router/index.ts:13-49` 基于 `userStore.isLoggedIn` 和 `userStore.userRole`，但这两个值来自 localStorage，可被任意篡改

### 2.8 前端 API 调用不携带认证信息

- `vue-kecheng/src/api/backend.ts:85-100` 的 `requestJson` 函数仅设置 `Content-Type`，无 `Authorization: Bearer <token>`
- `types/user.ts:23-25` 的 `AuthUser` 接口含 `token` 字段，`user.ts:11` 的 store 也维护 `token` ref，但**该 token 从未在任何 API 调用中被附加到请求**
- 无 401 拦截/重定向

### 2.9 密钥管理 — 严重泄露风险

- 根目录 `.env:8`：`LLM_API_KEY=sk-49051c0b0c5541f8b470006f73a5b5a0`（真实 DeepSeek API Key 明文）
- 根目录无 `.gitignore`，`.env` 极可能被 git 跟踪并提交
- `backend/.env` 同样含明文密钥
- `config.py:35-41`：`SECRET_KEY` 未配置时用 `'cpa-agent-insecure-default-do-not-use-in-production'`，仅 warning 不阻止启动
- `cors_middleware.py:17`：默认 `allowed_origins = ['*']`，任意网站可跨域调用

---

## 三、严重等级分类

### P0（立即修复，已构成实际泄露）

1. 根目录 `.env` 包含真实 DeepSeek API Key `sk-49051c0b...` — 应立即在 DeepSeek 控制台吊销并重新生成
2. 根目录无 `.gitignore`，`.env` 极可能被 git 提交 — 应立即 `git rm --cached .env` 并添加 `.gitignore`
3. `backend/.env` 同样含明文密钥

### P1（高危，部署即被攻破）

4. 后端无任何认证机制，所有 API 开放 — 至少加 JWT 中间件 + `@login_required` 装饰器
5. admin API 无权限校验 — 加 `@admin_required` 装饰器，校验 `user_account.role='admin'`
6. `favorites.py` / `learning.py` 的 IDOR 越权 — `user_id` 必须从 token 中提取
7. MySQL 用 root 连接 — 创建 `cpa_app` 专用账号，仅授予 `cpa_agent.*` 的 SELECT/INSERT/UPDATE/DELETE
8. Redis 无密码 — 启动时加 `--requirepass`，连接 URL 加密码

### P2（中危，需补齐）

9. 实现后端 `/api/auth/login`、`/api/auth/logout`、`/api/auth/refresh` 接口
10. 实际使用 bcrypt 生成和验证 password_hash（替换占位符）
11. 在 admin API 中写入 `admin_audit_log` 表
12. 前端 `requestJson` 注入 `Authorization: Bearer <token>` 头
13. 前端添加 401 拦截器自动跳转登录
14. 前后端角色定义对齐（补齐 TEACHER 或两边都删除）
15. CORS `allowed_origins` 收紧为前端实际域名列表
16. `SECRET_KEY` 未设置时直接抛错拒绝启动
17. 生产环境 `CPA_DEMO_MODE=false` 强制严格校验

### P3（低危，长期改进）

18. 实现 token 刷新机制（access token + refresh token）
19. 接入限流（如 flask-limiter）防止暴力登录
20. 实现 password 重置流程
21. 增加 CSRF 防护（若用 cookie session）
22. 数据库敏感字段（phone、email）查询时脱敏

---

## 四、建议实施路径

### 阶段一（最小可用鉴权）

- 创建根目录 `.gitignore`，吊销泄露的 API Key
- MySQL 创建 `cpa_app@'%'` 账号，授予 `cpa_agent.*` 读写权限
- Redis 加 `--requirepass`
- 实现 `/api/auth/login` 接口（bcrypt 校验 + JWT 签发）
- 实现一个 `@login_required` 装饰器 + `before_request` 钩子
- 在 admin 蓝图全部加 `@admin_required`
- 前端 `requestJson` 注入 token

**验证标准**：
- 未登录访问 `/api/admin/*` 返回 401
- 登录后访问 `/api/admin/*` 返回 200
- `user_account.last_login_at` / `last_login_ip` 被正确更新
- `admin_audit_log` 表有记录

### 阶段二（完善 RBAC 和审计）

- `favorites.py` / `learning.py` / `profile.py` 从 token 提取 user_id
- 实现 `/api/auth/refresh`
- admin API 写入 `admin_audit_log`
- 前端 401 拦截 + 自动刷新
- 收紧 CORS
- 实现 `teacher` 角色（如业务确实需要）

**验证标准**：
- 用 student 账号访问 `/api/admin/*` 返回 403
- 用 teacher 账号访问 `/api/admin/*` 返回 403
- `admin_audit_log` 表记录每个 admin 操作的 actor/target/action/timestamp
- 前端 401 时自动跳转登录页

---

## 五、暂不实施的理由

1. **当前项目阶段**：竞赛/研究向实际部署过渡阶段，后端虽已实现业务 API 但前端仍是 mock 演示，未对外暴露
2. **优先级**：数据隔离配置（避免硬嵌入）是更基础的工作，应在权限分配之前完成
3. **风险可控**：后端目前仅在本地开发环境运行，未部署到公网

## 六、启动条件

满足以下任一条件时启动本项工作：
- 数据隔离改造完成
- 后端准备对外暴露（部署到公网或局域网共享）
- 前端开始接入真实后端 API
- 出现安全事件或合规要求

---

## 七、附：业务权重/魔数迁入 YAML（数据隔离后续改进项）

> 本节记录数据隔离改造（2026-07-14 完成）中识别但未实施的业务规则硬编码问题。
> 这些不属于敏感配置，但属"业务数据硬嵌入"，需在业务逻辑稳定后统一迁入 YAML。

### 7.1 已完成的数据隔离改造（2026-07-14 起）

| 阶段 | 改造内容 | 涉及文件数 |
|------|---------|-----------|
| 阶段一 | scripts/ 中密码硬编码改为走 `_db_helper` + `Config` | 13 个脚本 + 新增 `_db_helper.py` |
| 阶段二 | Windows 绝对路径改为基于 `__file__` 推导 | `run.py`、`test_docker_full.py` 等 |
| 阶段三 | 服务地址/CORS 硬编码改为走 `Config` | `embedding_service.py`、`cors_middleware.py`、`config.py`、`__init__.py`、`.env.example` |
| 阶段四 (进行中) | 业务型数据列表迁入 `dictionaries/*.yaml` | 见 7.2 进度 |

**验证结果**：
- `scripts/` 目录零密码字面量（`!Rootmysql123`、`neo4jpass` 已全部移除）
- `backend/` 目录零 Windows 绝对路径
- CORS 支持通过 `CORS_ALLOWED_ORIGINS` 环境变量配置，白名单/黑名单行为正确
- 后端正常启动，API 响应正常，CORS 头按预期返回

### 7.2 阶段四进度（业务型数据迁 YAML）

> 用户分类原则：
> - **业务型数据**（技能/角色/专业/证书列表）→ `config/defaults/dictionaries/*.yaml`
> - **调参数据**（阈值/权重/评分表）→ `config/defaults/rules/*.yaml`
> - **环境变量**（密码/连接串）→ `.env`（已处理）

#### 7.2.1 已完成

| 子阶段 | 改造内容 | 文件 |
|-------|---------|------|
| 4.1 | `admin_kb.py:_fallback_extract` 的 `known_skills`/`known_roles` 硬编码列表迁移到 YAML | `dictionaries/position_lexicon.yaml`（新建，27 个岗位名按 6 类组织）；`dictionaries/skill_lexicon.yaml`（补充 CUDA/Transformer/K8s/SLAM/ROS）；`dictionary_loader.py`（新增 `get_known_skills_flat()` 和 `get_position_names_flat()`）；`admin_kb.py` 改为走 loader |
| 4.2 (P0) | `skill_extractor.py` 的 `SKILLS`/`ALIASES`/`DEFAULT_PROFICIENCY_LEVELS` + 上下文关键词硬编码字典迁移到 YAML | `dictionaries/skill_extractor_lexicon.yaml`（新建，14 分类 332 技能 + 65 别名 + 4 熟练度 + 14 上下文关键词）；`dictionary_loader.py`（新增 `get_skill_extractor_skills/aliases/proficiency_levels/context_keywords()`）；`skill_extractor.py` 改为走 loader，保留 `SKILLS`/`ALIASES`/`DEFAULT_PROFICIENCY_LEVELS` 类属性名以保证外部引用兼容 |
| 4.3 (P1) | `llm_skill_extractor.py` 的 6 项硬编码 + `entity_normalizer.py` 的 5 项种子数据迁移到 YAML | `dictionaries/llm_skill_aux.yaml`（新建，含 `tech_context_negative` 16 条 + `soft_skill_keywords` 39 条 + `high_confidence_skills` 40 条 + `level_confidence_mapping` + `soft_skill_discount` + `default_confidence` + `category_map` 9 类 140 技能）；`dictionaries/entity_seeds.yaml`（新建，含 `skill_category_map` 7 条 + `common_majors` 12 条 + `common_positions` 10 条 + `degree_seeds` 4 条 + `cert_seeds` 10 条）；`dictionary_loader.py`（新增 11 个加载函数）；`llm_skill_extractor.py` 改走 loader，保留 `TECH_CONTEXT_NEGATIVE`/`SOFT_SKILL_KEYWORDS` 模块级常量名以兼容外部引用；`entity_normalizer.py:seed_canonical_entities` 改走 loader |
| 4.4 (P2) | `portrait_generator.py` 的 D3/D4 权重 + 分类/等级权重 + 学术评分迁移到 YAML | `rules/scoring_tables.yaml`（新增 `portrait_generator` 节：`d3_quality_level_score` + `d3_sub_weights` + `d3_sub_required` + `d4_sub_weights` + `d4_defaults` + `skill_category_weights` 14 条 + `skill_level_weights` 5 条 + `academic_score` 阈值表）；`portrait_generator.py` 的 `_score_quality_item`/`_calc_job_d3`/`_calc_job_d4`/`_get_category_weight`/`_get_level_weight`/`_academic_score` 6 处全部改走 `get_rules_config().scoring_tables` |
| 4.5 (P3) | `matcher.py` 的 D3 软硬编码迁移到 YAML + 修复 `match_level` 阈值不一致 bug | `rules/thresholds.yaml`（新增 `match_level` 节统一阈值 `{excellent: 85, good: 70, fair: 60}` + `d3` 节：`level_map` 3 项 + `keywords` 4 组（communication/teamwork/stress_tolerance/responsibility）+ `keyword_score_factor/min/cap` 魔数 + `rule_bonus` 4 项 + `score_formula` 总分公式参数）；`loader.py`（新增 `match_level_thresholds`/`d3_params` 属性）；`matcher.py` 的 `_calc_d3_detailed` 改走 `self._d3_params`，模块级 `_get_match_level` 改走 `get_rules_config().match_level_thresholds`；`pipeline_ext.py:2474-2483` 修复 `>=75 → 良好` 的硬编码 bug，改为调用 `matcher._get_match_level` 统一走权威源 |
| 4.6 | 模型缓存路径迁移（缓解 C 盘空间压力 + 修复 YOLO 路径 bug + 统一本地 LLM 权重存放） | `run.py`（新增 `_MODELS_ROOT` + 5 个环境变量：`MODELSCOPE_CACHE`→`models/mineru/`、`HF_HOME`/`SENTENCE_TRANSFORMERS_HOME`/`HF_HUB_CACHE`/`TRANSFORMERS_CACHE`→`models/embeddings/` 子目录）；`embedding_service.py`（新增 `_EMBEDDING_CACHE` 常量，`SentenceTransformer` 显式传 `cache_folder`）；`l0_full_parser.py`（新增 `_YOLO_MODEL_PATH` 常量指向 `models/yolo/SmartResume/yolov10/best.onnx`，修复原 `backend/app/model/SmartResume/yolov10/` 路径错误导致的 YOLO 永久不可用 bug）；`.env`/`.env.example`（新增模型缓存路径配置节）；`.gitignore`（新增 `models/mineru/` 忽略规则，移除已清理的 `model/` 和 `modelscope_cache/` 规则）；物理迁移：`modelscope_cache/` (3.06GB) → `models/mineru/`，`model/ResumeNER/yolov10/best.onnx` (253MB) → `models/yolo/SmartResume/yolov10/`，`model/Qwen3.5-4B/` (8.7GB, 17 文件) → `models/llm/Qwen3.5-4B/`，`model/ResumeNER/Qwen3-0.6B/` (1.13GB, 9 文件) → `models/llm/Qwen3-0.6B/`（用于本地实体和关系识别，代码加载入口待后续接入）；`model/` 目录（含 ResumeNER git clone 元数据）已完全清理 |
| 4.7 | API 模块共享 helper 提取（消除 `backend/app/api/` 下 13 个文件的错误处理/JSON 解析/维度字典重复代码） | `api/_helpers.py`（新建，4 个纯函数：`safe_error(context, error, *, message, code)` 统一错误响应+日志；`parse_json_field(value, default)` 处理 MySQL JSON 列的字符串序列化；`build_dimensions_d1_d4(d1..d4)` 构造 D1-D4 维度字典（消除 match.py ×2 + profile.py ×1 重复）；`build_sub_dimensions(skill, cert, ...)` 构造七维子维度字典（消除 match.py ×2 + profile.py ×1 重复））；改造 12 个 API 文件全部接入 helper：`match.py`/`profile.py`/`report.py`/`career.py`/`agent.py`/`pipeline.py`/`admin.py`/`admin_kb.py`/`favorites.py`/`graph.py`/`learning.py` 共替换 50+ 处 try/except 错误响应模板 + 20+ 处 `isinstance(str)+json.loads+except:pass` JSON 解析模板 + 4 处维度字典字面量；`pipeline_ext.py` 保留其本地 `_safe_error(error, context)` helper（签名顺序与共享版相反，40+ 调用点改写收益微小，按"简单功能直接实现"原则保留）；移除 9 个文件中冗余的 `from ..exceptions import ErrorCode` 导入；移除 `admin.py` 中 3 处函数内 `import json` 内联导入（顶部已有）；保留特殊错误场景的 `message=` 参数覆盖（如 `semantic_explore` 的 "服务器内部错误"、`graph.build_graph` 的 "任务创建失败，请稍后重试"） |

**验证结果**：
- `admin_kb._fallback_extract` 函数体零硬编码列表，全部走 `dictionary_loader` 加载
- 技能词典从 24 条扩展到 153 条（跨 6 大类），岗位词典从 19 条扩展到 27 条（跨 6 类）
- 端到端测试通过：smart-upload 接口在 LLM 不可用时正确 fallback，13 个技能 + 3 个角色成功匹配并写入数据库
- `SkillDictionary.SKILLS` 载入 332 条技能（跨 14 分类），`ALIASES` 载入 65 条别名，`DEFAULT_PROFICIENCY_LEVELS` 4 级
- 抽取行为无回归：13 个测试技能按预期抽取，置信度计算正确（"熟练掌握" → 0.65 = 0.5 base + 0.15 positive + 0.1 SKILLS bonus）
- 后端启动正常，`/api/pipeline/ext/extract/skills` 接口端到端测试通过，返回 9 个技能分类置信度正确
- `llm_skill_extractor` 行为无回归：`_detect_skill_type`/`_calc_confidence`/`_categorize`/`_is_hallucination`/`_get_high_confidence_skills` 全部测试通过（hard=0.95, soft=0.76, default=0.5）
- `entity_normalizer.seed_canonical_entities()` 端到端测试通过：`/api/pipeline/ext/normalizer/init` 接口成功初始化表 + 写入种子数据，YAML 加载 + params 组装 + DB 写入链路全部正常
- `portrait_generator` 行为无回归：`_get_category_weight`/`_get_level_weight`/`_calc_job_d3`/`_calc_job_d4`/`_academic_score` 全部测试通过（编程语言=1.0, 精通=1.2, GPA 3.9→25, rank 3%→25）
- `/api/pipeline/ext/portrait/student` 接口端到端测试通过：4 个技能生成画像，D1=0/D2=35/D3=20/D4=0，综合分 16.2，D3 "弱"=5（来自 YAML `d3_quality_level_score`）
- P3 match_level bug 修复验证：`_get_match_level(75)` 返回 "良好"（统一走 `thresholds.yaml` 的 `good: 70` 阈值），`pipeline_ext.py` 与 `matcher.py` 行为一致
- P3 D3 硬编码迁移验证：`_calc_d3_detailed(None)` 返回 50.0（参数全部从 `self._d3_params` 加载），无回归
- 模型缓存路径迁移验证：5 个环境变量全部指向项目内 `models/` 子目录（覆盖系统级 `MODELSCOPE_CACHE`）；`_YOLO_MODEL_PATH` 文件存在（253.49 MB），`_check_yolo()` 返回 True（修复了原路径错误导致 YOLO 永久不可用的 bug）；3.06GB `modelscope_cache/` 已迁移到 `models/mineru/`；本地 LLM 权重 `Qwen3.5-4B` (8.7GB, 17 文件) 和 `Qwen3-0.6B` (1.13GB, 9 文件) 已迁移到 `models/llm/`；原 `model/` 目录（含 ResumeNER git clone 元数据）已完全清理；`.gitignore` 正确忽略 `models/{embeddings,llm,mineru,yolo}/` 下的实际模型文件，仅保留 `.gitkeep`
- API helper 提取验证：4 个 helper 单元测试全部通过（`safe_error` 默认/自定义消息、`parse_json_field` 4 种输入、`build_dimensions_d1_d4` 4 维结构、`build_sub_dimensions` 7 维结构）；13 个 API 文件语法检查 + 12 个模块导入检查全部通过；Flask app 创建成功，148 路由全部注册；7 个改造端点（admin/knowledge-base、saved-jobs、learning/courses、graph/overview、career/landscape、pipeline/list、graph/skill-categories）结构化测试全部返回 200 + `success` 字段；9 个文件 `from ..exceptions import ErrorCode` 导入已全部移除（无残留 `ErrorCode` 引用）

#### 7.2.2 待改进项（未实施）

以下业务权重/魔数散落在代码中，应迁入 YAML 统一管理：

| 文件:行号 | 硬编码内容 | 分类 | 建议 |
|----------|-----------|------|------|
| `pipeline_ext.py` 多处 | `{"D1": 0.25, "D2": 0.35, "D3": 0.20, "D4": 0.20}` | 调参 | 统一改读 `matching.yaml` 的 `dimension_weights` |
| `skill_extractor.py:539` | `confidence = base + positive*0.15 - negative*0.1` | 调参 | 命名并迁入 YAML |
| `yolo_layout_detector.py:64-67` | `INPUT_SIZE=640, CONFIDENCE=0.25, IOU=0.45` | 调参 | 迁入 `yolo.yaml` |
| `seed_from_mock.py` / `seed_seven_dim.py` | 七维 D5-D10 权重 | 调参 | `matching.yaml` 增加 `seven_dim_weights` 节 |

**未实施理由**：这些改动涉及业务逻辑正确性回归测试，待业务逻辑稳定后按优先级分批改造。P3（`match_level` 阈值不一致 bug 修复 + `matcher.py` D3 硬编码迁移）已在 4.5 完成，本节已移除对应条目。

### 7.3 多租户数据隔离（长期规划）

当前架构是单租户单库。未来如需多租户，需关注：
- MySQL：新增 `tenant_id` 列或按 `WORKSPACE_ID` 切换 database
- Redis：`REDIS_URL` 的 `db=0` 写死，需按租户分配 db 号或 key 前缀
- Milvus：`./milvus_data/milvus.db` 单文件，需按 `WORKSPACE_ID` 切换路径或 collection 前缀
- MinerU 输出：`./mineru_output` 共享目录，需改为 `Config.OUTPUT_DIR / workspace_id / "mineru_output"`
- Uploads：`UPLOAD_FOLDER` 共享，需按 `tenant_id` 子目录隔离
