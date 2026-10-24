<div align="center">

# 职导星图 · CPA-Agent

**面向大学生全周期职业规划的多智能体决策平台**

</br>
<em>An Open-Source Multi-Agent Career Planning Platform for University Students</em>

[![License](https://img.shields.io/badge/License-AGPL--3.0-blue?style=flat-square&logo=gnu&logoColor=white)](./LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/WHaoxM/cpa-agent?style=flat-square&color=DAA520)](https://github.com/WHaoxM/cpa-agent/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/WHaoxM/cpa-agent?style=flat-square)](https://github.com/WHaoxM/cpa-agent/network)
[![GitHub Issues](https://img.shields.io/github/issues/WHaoxM/cpa-agent?style=flat-square)](https://github.com/WHaoxM/cpa-agent/issues)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3.5-42b883?style=flat-square&logo=vue.js&logoColor=white)](https://vuejs.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-Dev%20DB%20Stack-2496ED?style=flat-square&logo=docker&logoColor=white)](./DEPLOY.md)

[![Live Demo](https://img.shields.io/badge/Live%20Demo-GitHub%20Pages-6E40C9?style=flat-square&logo=github&logoColor=white)](https://whaoxm.github.io/cpa-xingtu/)
[![Frontend](https://img.shields.io/badge/Frontend-cpa--xingtu-42b883?style=flat-square&logo=vue.js&logoColor=white)](https://github.com/WHaoxM/cpa-xingtu)

[中文](./README.md) | [English](./README.en.md)

</div>

## 📸 界面预览

主仓未分发截图文件；请打开[独立前端仓库](https://github.com/WHaoxM/cpa-xingtu)的 Mock 演示，或按下文在本机运行查看。不要把在线静态演示当成后端真实数据验收。

## ⚡ 项目概述

**职导星图（CPA-Agent）** 是一个面向大学生的多智能体职业规划决策平台——不是单一的岗位推荐页，而是从简历解析到职业报告生成的**完整决策闭环**。

它将简历解析、四维能力画像、岗位需求建模、职业知识图谱、向量检索、检索增强生成与多智能体编排结合在一起，帮助学生在「诊断 → 匹配 → 学习 → 报告」链路中获得数据驱动的职业指导。各环节的真实数据与外部依赖须按下文分别复现和验收。

> 你只需：上传一份 PDF 简历，或在对话中描述你的背景与目标
> CPA-Agent 将返回：结构化的四维能力画像、可解释的人岗匹配结果、可执行的职业发展报告

### 🎯 我们的愿景

职导星图致力于打破传统职业指导的局限，让每一个"如果"都能看见结果：

- **于学生**：基于真实简历与岗位数据的个性化诊断，而非泛泛的职业鸡汤
- **于高校**：批量解析、标准化画像与报告生成，适合就业指导中心规模化使用
- **于研究**：L0–L4 管线、多模式 Agent、NSLE 实体抽取均有清晰模块边界，便于论文实验与消融

从简历输入到职业报告，每一步都有 Agent Trace 可观测，推荐结果可追溯到图谱节点与匹配权重，而不是黑盒式的一句"建议你去做产品经理"。

## 🌐 在线演示

体验前端在线 Demo（主要使用 Mock 数据，无需后端）：

👉 **[https://whaoxm.github.io/cpa-xingtu/](https://whaoxm.github.io/cpa-xingtu/)**

**演示账号：**

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 学生 | student001 | 123456 |
| 管理员 | admin001 | 123456 |

以上账号只属于前端 Mock/Pages 演示；真实后端 strict 模式没有内置演示账号。

## ✨ 核心亮点

两个能力决定了 CPA-Agent 与通用 Chatbot 的本质差异：

- 🧭 **L0–L4 分层管线。** 简历解析、图谱构建、画像生成、人岗匹配、报告生成有独立业务边界与 `pipeline_task` 状态追踪。专用 L0 简历导入与 L3 匹配具备 MySQL 持久意图、Celery 消费和重放；确认后的 L2 画像、简历范围诊断及画像快照是下文所述需核对 `0006`/`0007` 迁移与代码版本的扩展。通用旧 Pipeline 入口仍以同步为主，其他旧异步触发入口在 strict 模式返回 503；L1/L4 的可靠异步调度仍待完成。
- 🤖 **多模式 Agent + 13 工具。** 默认运行时为 **Supervisor 编排器**（Ask / Agent / Experts 三种交互模式，Plan 步骤 + 黑板 + 记忆分库 + PreToolUse 门禁）。旧 LangGraph（单体）/ ReAct 引擎默认关闭，仅 demo 开关显式启用。Agent 可调用图谱查询、语义检索、管线触发等 13 个工具，在推理与确定性业务逻辑之间保持边界清晰。

## 🔄 工作流程

1. **简历解析（L0）**：PDF/Docx → 版面检测 → NER 实体抽取 → `student_profile`
2. **图谱构建（L1）**：简历/JD 实体 → Neo4j 节点与关系 → 技能-岗位-职业路径网络
3. **画像生成（L2）**：学生 ID + 技能 → 四维评分（D1 基础/D2 技能/D3 素养/D4 潜力）→ `student_portrait_4d`
4. **人岗匹配（L3）**：学生画像 + 岗位画像 → 加权匹配分 + 技能差距 + 晋升路径
5. **报告生成（L4）**：学生 + 目标岗位 → 六章 `career_report`（含学习路径与评估周期）

## ⚙️ 运行环境（Agent / 开发者必读）

> 陌生 Agent 在本机执行任何后端命令或测试前，**必须先读本节**。默认的 `python` 命令指向系统 Python 3.11，**没有安装本项目依赖**，直接跑测试会立刻报 `ModuleNotFoundError: No module named 'flask'`。

### 权威 Python 运行时

后端运行与测试统一使用 conda 环境 **`mkg`**（Python 3.10）：

```bash
conda activate mkg
python --version   # Python 3.10.x
```

Windows 下若当前 shell 没有 `conda`，可直接用解释器绝对路径：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -m pytest backend/tests
```

> ⚠️ conda 环境 `cpa-agent`（Python 3.11）依赖不完整（缺 langgraph），**不要**用它运行后端或测试。

### 安装依赖

```bash
conda activate mkg
pip install -r backend/requirements.txt
```

### 运行后端

```bash
conda activate mkg
cd backend
python run.py      # http://localhost:5001
```

`run.py` is the Flask development server. For the protected Windows host service, use `python -B serve_waitress.py` from `backend/`; it validates production configuration and schema head, then binds only to a numeric loopback address. See [DEPLOY.md](DEPLOY.md).

严格模式的专用 `POST /api/pipeline/l3/run-match` 使用 MySQL 持久意图和 `cpa.pipeline` Celery 队列，不随 Flask 进程内执行。先按 [MySQL 统一迁移](docs/runbooks/mysql-migrations.md) 在 `backend/` 运行 `python -B -m alembic -c alembic.ini upgrade head`；再分别启动 `python -B -m celery -A app.services.agent_runtime.media.jobs:celery_app worker --pool=solo --concurrency=1 --queues=cpa.pipeline,cpa.media,cpa.graph --loglevel=INFO` 和 `python -B -m celery -A app.services.agent_runtime.media.jobs:celery_app beat --loglevel=INFO`。后两个队列分别消费 Agent 附件和个人图任务。Beat 默认每 60 秒触发有界 MySQL 恢复扫描；缺 Beat 时故障任务只能按 [Pipeline 派发与恢复边界](docs/guides/pipeline-dispatch-boundary.md) 手动重放。其他旧启动入口仍不可用。当前真实岗位发布目录为空，不能把接口可运行理解为已有可匹配岗位。

职途导航的真实简历导入另有专用 `POST /api/profile/student/upload?async=1`：文件或粘贴文字经同一 L0 持久任务进入 `cpa.pipeline` worker，由文档、结构、实体、证据四个实际职责接力；`GET /api/pipeline/trace/<task_id>` 提供归属校验后的交接轨迹和四个已完成检查点百分比。**只启动 HTTP 服务不会执行异步解析**，还须按上段启动 `cpa.pipeline` worker；未接单的已保存任务会保留编号，页面等待一段时间后提示检查解析服务，无需重复上传。Beat 负责异常后的定时重派，不能代替 worker。解析前沿用演示版页面布局，处理中在原预览/仪表盘位置动态呈现；候选仍须人工核对，轨迹/百分比不代表已评分。首次启用须完成 Alembic 迁移，详见 [L0 多 Agent 轨迹](docs/guides/resume-agent-team-trace.md)。

确认简历后的 L2 画像、简历范围诊断任务及画像只追加快照属于需单独核对版本的扩展；**新克隆不能仅凭本页假定已经包含**。只有检出的版本同时包含对应后端代码和 `0006`/`0007` Alembic 迁移，且已实际迁移数据库并运行 worker/Beat，才能按 `GET /api/profile/student/workbench`、`GET /api/profile/student/portrait-snapshots` 验收 owner 范围的两份成果、`analysis_run` 与新画像快照。未知维度继续留空，不伪造综合分；旧版快照只读对照，不参与当前评分。历史遗留画像的安全迁移与真实逐条工具过程仍在后续阶段，见 [状态契约](docs/specs/student-workbench-state-contract.md) 与 [迁移手册](docs/runbooks/mysql-migrations.md)。

这条简历上传的 multipart 临时文件会溢写到 `backend/uploads/` 所在卷，而不是依赖系统临时目录的剩余容量；该卷仍须为最大 50 MB 请求及解析产物留足空间。存储耗尽时返回明确错误且不创建解析任务，不能把上传 500/202 当成已经开始 OCR。扫描 PDF 还须运行真实 OCR Sidecar，详见 [附件完整性与 OCR](docs/guides/attachment-completeness.md)。

已确认简历若缺少职业素养的原文证据，`cpa-xingtu` 职途导航允许学生另行填写并撤回四项具体经历；服务端明确标记为“用户自述”，重新计算候选画像，但不把它写回简历原文，也不替代缺失页面、学校档位或专业类别的核验。岗位正式匹配仍依赖可验证的画像和岗位发布来源。

旧版二进制 `.doc` 简历需预装 Java 17+，并为 Flask 和 Pipeline worker 配置 `CPA_L0_DOC_TIKA_JAR` 指向已核验的 Apache Tika 独立 JAR；缺组件会明确失败，不会误按 DOCX 处理。Tika 只抽取文本，图片与页码仍标记未核验。安装、校验和重传方法见 [旧版 DOC 简历](docs/guides/legacy-doc-resume.md)。

仓库根目录 `.env` 的 `CPA_DEMO_MODE=false` 表示 strict 身份模式：默认不会从请求体、查询参数或内置学号自动推断用户。平台自建账号的 `/api/auth/csrf`、`/register`、`/login`、`/change-password`、`/logout`、`/me` 使用 MySQL `user_account` 与 Redis 服务端会话；已登录改密须提供当前密码，成功后所有设备重新登录。启用前须设置随机、至少 32 字符的 `SECRET_KEY`，生产须使用 HTTPS、`FLASK_DEBUG=false`、`CPA_AUTH_COOKIE_SECURE=true`（默认），并将 `CORS_ALLOWED_ORIGINS` 设为真实前端 HTTPS Origin、不得为 `*`。现有 Cookie 为 `SameSite=Lax`，生产优先同源或同站 HTTPS 代理；跨站前端不能只靠配置 CORS 接通账号会话。仅本机 HTTP 调试时才将 `CPA_AUTH_COOKIE_SECURE=false`；管理员使用可信本机 CLI `flask --app app provision-admin --username <name>` 开户，不提供公开管理员注册。详情和未覆盖边界见 [自建账号规格](docs/specs/self-managed-account-auth.md)。只有**未配置正式账号认证适配器**的本机调试环境，显式设置 `CPA_DEV_AUTH=1` 后，loopback 请求才可通过 `X-Dev-Student-Id` 注入临时学生 Principal；账号适配器一旦启用，该开发头无效，不能获得管理员权限。

岗位发布的正式账号管理端提供 `/api/admin/job-publications` 的预检、精确摘要确认、版本回读与撤回；见 [HTTP 发布契约](docs/specs/admin-job-publication-http.md)。这不是原网页真实性或招聘有效期的自动证明。旧本机 `backend/scripts/mysql/publish_job_catalog.py` 仍可直接发布，但 `reviewed_by/reviewed_at` 来自其 JSON 输入，**不经过账号审核或新的原子审计**；只限可信运维环境与独立人工核对，不能与正式 HTTP 审核记录等价。
正式管理员还可经只读 `/api/admin/job-reviews` 查看模型岗位审查的来源、阻断原因和候选摘要；它不授予发布权，不自动核实当前招聘是否仍开放，见 [岗位模型审查](docs/guides/job-catalog-model-review.md)。
管理员 dataset/KB 上传现可用受限 multipart 将 JSON 岗位列表或 UTF-8 文本**私有暂存**并回读摘要；不创建可信岗位行、不公开也不建图，既有 MySQL volume 须先迁移，见 [管理员来源暂存](docs/specs/admin-source-staging.md)。
正式管理员可将一条 `ds_` 暂存岗位以服务端文件和记录摘要绑定到发布预检，再经人工确认发布；上传本身仍不授予官方来源资格，前端自报的 dataset 绑定无效。见 [数据集来源绑定](docs/specs/admin-dataset-publication-binding.md)。
学校档位人工修正仅接受可核验的教育部或已登记学校官网原文；首期只支持有明确原文的 985、211、双一流，并绑定正式管理员审核、当前已确认简历版本及可撤回证据。当前 live 数据库尚未迁移到 `0005`，审核入口不可用；旧无审核画像路径保持可用。正式启用需维护窗口迁移与业务验收，见 [学校档位审核](docs/specs/school-tier-review.md)。
经正式管理员核对固定官方原文、精确摘要及截止时间后，评分器暂不支持的岗位可通过独立 `/api/admin/job-source-listings` 作为**仅供查看来源**的条目展示于公开岗位目录；此类 `source_csb_` 岗位明确标为不可评分/匹配，L3/L4 在建任务前拒绝，且不自动转成可评分岗位。公共读取使用最长 5 分钟的 Redis 后台来源核验缓存；上游/缓存故障会隐藏这类岗位，仍保留平台已发布岗位并标记部分结果。后台核验复用上文的 Beat 和 `cpa.pipeline` worker；缺任一进程不会刷新。既有 MySQL volume 必须先执行加表迁移，见 [来源专用岗位规格](docs/specs/source-only-job-catalog.md)。

既有 Docker MySQL volume **不会重跑初始化 SQL**。首次启用新后端或升级现有库时，先备份、停写，再在 `backend/` 目录以 `mkg` 环境执行 `python -B -m alembic -c alembic.ini upgrade head` 并核对 `current` 与 `heads`；新建 volume 初始化后也执行同一命令。它集中接管账号 `auth_epoch`、管理员来源、Pipeline 与投影等结构变更；不再反复修改初始化 SQL 或逐个运行旧迁移脚本。缺账号列/触发器时认证仍返回 503，不降级为开发头身份。操作与失败恢复见 [MySQL 统一迁移](docs/runbooks/mysql-migrations.md) 与 [自建账号规格](docs/specs/self-managed-account-auth.md)。

### 运行测试

```bash
conda activate mkg
python -B -m pytest backend/tests   # 在仓库根目录执行
```

- 纯单元测试无需外部服务；含 DB 的集成用例依赖本机 Docker 容器（MySQL `localhost:13306`、Neo4j `localhost:17687`、Redis `localhost:16379`）。
- 容器未启动时，`test_hybrid_retrieve_explain.py`、`test_demo_endpoints.py` 会出现连接拒绝类失败，属环境问题而非代码回归。
  - 本机完整回归（2026-10-14，MySQL/Neo4j/Redis 已启动）：设置 `CPA_TEST_PIPELINE_MYSQL=1`、`CPA_TEST_LIVE_MYSQL=1`、`CPA_TEST_LIVE_PUBLIC_GRAPH=1` 后运行整个 `backend/tests`，结果 **1156 passed / 24 skipped**；跳过项仍需按对应外部依赖单独验收。这不代表尚未签审的真实简历 CER、可信岗位发布或生产部署验收。
- 根目录 `.env` 当前 `CPA_DEMO_MODE=false`（strict 模式）：用户资源 API 需要认证适配器提供 Principal，否则返回 401；联调前请先启动数据库容器。

### 数据依赖

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d mysql neo4j redis
```

## 🚀 快速开始

先选复现范围，不要把静态演示与真实后端混为一条路径：

| 路径 | 步骤 | 能证明什么 |
|---|---|---|
| 纯前端演示 | 克隆独立的 [cpa-xingtu](https://github.com/WHaoxM/cpa-xingtu)，Node `^20.19.0 || >=22.12.0`，在该仓库执行 `npm ci`、`npm run dev:mock` | `http://localhost:5173` 的 fixture/Mock 页面；不需要 Docker、LLM 或本仓后端 |
| 本机后端基础检查 | 从本仓 `.env.example` 复制 `.env`；用 Python 3.10 安装 `backend/requirements.txt`；启动 MySQL/Neo4j/Redis，执行 Alembic 迁移，然后在 `backend/` 运行 `python -B run.py` | `http://127.0.0.1:5001/health` 与路由可用；不等于异步/模型/真实业务成功 |
| 真实前后端联调 | 在上述基础上设 `CPA_DEMO_MODE=false`、强 `SECRET_KEY`、LLM 凭据；按 [DEPLOY.md](DEPLOY.md) 启动三队列 worker 和 Beat；前端用 `npm run dev:backend` | 通过注册/登录后按 owner 核验简历、任务和最终成果；未发布可信岗位时无法复现正式匹配 |

**本机复现命令与生产阻断项见 [DEPLOY.md](DEPLOY.md)；镜像改造路径见 [生产级镜像改造计划](docs/specs/production-image-hardening.md)。** 新克隆时模板 `.env.example` 默认为 demo，当前工作机的私有 `.env` 为 strict；两者不能混用推断。前端是独立仓库，不在本仓的 Git 跟踪范围内。开发 Compose 只负责数据库。单机生产覆盖层已有显式迁移、Gunicorn HTTP、worker、Beat 与冷卷快照/新项目恢复工具，且默认不发布宿主机应用端口；但尚缺已发布的固定 digest 镜像、依赖锁定、HTTPS 代理、真实 Docker 恢复演练与完整业务验收，不能直接上线。

## 🏗️ 架构概览

```
Vue 3 前端 (cpa-xingtu)
        │ HTTP / SSE
        ▼
Flask 后端 (backend/app)
  API 路由 → 服务层 → Agent 运行时 (Supervisor 编排；Ask/Agent/Experts 模式)
                        │
                        ▼
              Tools (13) ──→ MySQL / Neo4j / Redis / Milvus
                        │
                        ▼
              L0–L4 Pipeline (简历→图谱→画像→匹配→报告)
```

**L0–L4 管线：**

| 层级 | 输入 | 输出 |
|------|------|------|
| L0 | PDF / 文本简历 | `student_profile` |
| L1 | JD / 简历文本 | Neo4j 节点与关系 |
| L2 | 学生 ID + 技能 | `student_portrait_4d` |
| L3 | 学生 + 岗位画像 | 匹配分、技能差距、职业路径 |
| L4 | 学生 + 目标岗位 | 六章 `career_report` |

## 📊 四维画像与人岗匹配

**竞争力 = D1×0.25 + D2×0.35 + D3×0.20 + D4×0.20**

| 维度 | 含义 | 示例 |
|------|------|------|
| **D1 基础要求** | 学历、专业、证书等硬约束 | 本科 / 计算机相关 |
| **D2 职业技能** | 技能覆盖率与熟练度 | Java、Spring Boot、MySQL |
| **D3 职业素养** | 软技能、项目与实习经历 | 团队协作、项目交付 |
| **D4 发展潜力** | 竞赛、论文、创新产出 | 蓝桥杯、开源贡献 |

匹配引擎结合加权评分、技能差距分析与 Neo4j 中的晋升 / 换岗路径，输出不只"匹配分"，还有"差什么、怎么补"。

## 🛠️ 技术栈

### 后端

- **Python 3.10+** / Flask 3.x
- **Supervisor 多模式 Agent 运行时** - Ask / Agent / Experts 编排（旧 LangGraph 单体 / ReAct 引擎默认关闭）
- **ReAct** - 兼容备引擎
- **SQLAlchemy** - ORM
- **PyMilvus Lite** - 向量数据库嵌入式模式

### 前端

- **Vue 3.5** + TypeScript
- **Vite 7** - 构建工具
- **Element Plus** - UI 组件库
- **D3.js / ECharts / Three.js** - 数据可视化
- **Pinia** - 状态管理
- **GSAP** - 动画引擎

### 数据层

- **MySQL 8.0** - 业务实体与关系数据
- **Neo4j 5.x** - 技能-岗位-职业路径知识图谱
- **Redis 7** - 会话管理与缓存
- **Milvus Lite** - 向量检索（BGE-M3, dim=1024）

### AI 模型

- **LLM** - OpenAI 兼容 API（DeepSeek / Qwen / Kimi / Ollama 等）
- **Embedding** - BAAI/bge-m3（多语言，1024 维）
- **简历解析** - MinerU + YOLOv10 布局检测 + Qwen3-SFT NER

## 📁 项目结构

```
cpa-agent/                  # 主仓库（后端 + 文档）
├── backend/                # Flask 后端
│   ├── app/                # 应用主目录
│   │   ├── api/            # API 路由
│   │   ├── models/         # 数据模型
│   │   ├── pipeline/       # L0-L4 管线
│   │   ├── services/       # 业务服务 (Agent/DB/Tools)
│   │   └── utils/          # 工具
│   ├── config/             # 配置加载器
│   ├── scripts/            # 数据库初始化脚本
│   └── tests/              # 测试
├── docs/                   # 项目文档
├── workspace/              # 工作区配置
├── docker-compose.yml      # Docker 编排
└── .env.example            # 环境变量模板

# 前端独立仓库：https://github.com/WHaoxM/cpa-xingtu
# 本地可克隆为同级目录 cpa-xingtu/（gitignore，不随主仓提交）
```

## 📌 当前状态

- **后端** Flask + Supervisor 多模式 Agent 运行时（Ask / Agent / Experts），Python 3.10+，默认端口 `5001`
- **前端** Vue 3 + TypeScript + Element Plus + D3 / ECharts / Three.js，开发端口 `5173`
- **数据层** MySQL 8.0 · Neo4j 5.x · Redis 7 · Milvus Lite / Server
- **简历解析** MinerU + YOLOv10 布局检测 + Qwen3-SFT NER + 实体归一化（auto 阶梯 = Parse Sidecar → PyMuPDF，MinerU 仅显式启用）
- **Agent 工具** 图谱查询、DB 查询、语义检索、管线触发、LLM 推理、JD 清洗、技能工具集等 13 个
- **输出模式** 四维画像、人岗匹配、技能差距、六章职业发展报告、SSE 流式对话
- **部署** Dev Docker Compose 只拉起 MySQL + Neo4j + Redis；生产候选 Compose 可显式迁移并分进程运行 HTTP/worker/Beat，但尚未完成 HTTPS 与恢复演练；前端独立部署至 GitHub Pages

## 💡 一个具体例子

学生上传简历，并对 AI 助手说：

> 我是计算机专业大三学生，做过两个 Spring Boot 后端项目，熟悉 MySQL 和 Redis，想毕业后做后端开发，但不太确定该冲大厂还是先去创业公司积累经验。

以下是产品展示用的**示意输出**，不是新克隆仓库自带数据可重复得到的分数或岗位；真实结果取决于已确认简历、模型、图谱和经审核发布的岗位：

```text
【画像摘要】D2 职业技能 72/100 · D4 发展潜力 68/100
【匹配岗位】Java 后端开发工程师（匹配度 81%）
【技能差距】
  · 需补强：分布式系统、消息队列（Kafka/RabbitMQ）
  · 已具备：Java、Spring Boot、MySQL、Redis
【职业路径】
  初级后端 → 中级后端（2–3 年）→ 架构师 / 技术负责人
【学习建议】
  1. 完成消息队列实战项目（建议 4–6 周）
  2. 补充系统设计基础（推荐课程见学习路径模块）
```

这不是泛泛的职业鸡汤，而是基于图谱、岗位画像与技能差距分析的可执行建议。

## 🎯 适用场景

- **高校就业指导中心** — 批量解析学生简历，生成标准化能力画像与报告
- **职业规划课程 / 竞赛项目** — 演示"知识图谱 + RAG + Agent"的完整工程落地
- **个人开发者** — 自建职业知识库，对接真实招聘 JD 数据，做个性化匹配
- **研究复现** — L0–L4 管线、NSLE 实体抽取、多模式 Agent 均有清晰模块边界，便于论文实验与消融

## 🤝 参与贡献

欢迎 Issue 与 Pull Request。建议流程：

1. Fork 本仓库，从 `main` 拉取最新代码
2. 在本地完成改动，并验证后端 API 或前端 `npm run type-check`
3. 提交 PR，说明改动范围与测试方式

较大改动（Schema 变更、新管线层级、Agent 工具）请先开 Issue 讨论。MySQL / Neo4j / Milvus 的 Schema 已冻结（v1.0），仅允许**新增**字段或节点，禁止破坏性修改。

## 🗺️ 路线图

规划中、尚未全部落地的能力：

- 跨会话风格记忆：画像与推荐随使用逐步个性化
- 历史记录增强：搜索、重新匹配、重新生成报告
- 本地 ASR / 语音输入职业咨询
- 更多行业 JD 数据源与图谱自动扩充
- 前端与后端 API 扩展联调（当前已接职业全景、AI 助手 demo、职途导航 Pipeline 任务，其余复杂页面仍部分使用 Mock）

## 📄 相关文档

| 文档 | 说明 |
|------|------|
| [当前工作入口](./docs/current-work.md) | 按任务选择现行文档与待核状态（日期快照） |
| [历史 Code Wiki](./docs/archive/2026-10/CODE_WIKI.md) | 2026-06 代码地图，已归档；不作现行架构或运行指南 |
| [CONTEXT.md](./CONTEXT.md) | 领域术语与项目决策上下文 |
| [DEPLOY.md](./DEPLOY.md) | 部署详细说明 |
| [cpa-xingtu（前端仓库）](https://github.com/WHaoxM/cpa-xingtu) | 前端模块、路由、演示账号与 Pages 部署 |
| [docs/](./docs/) | ADR、运行手册、规范与指南 |

## 📄 致谢

- 感谢四川轻化工大学（SUSE）对项目的支持
- 感谢所有参与设计、开发与测试的同学
- 感谢提供岗位数据与知识库资源的开源社区与竞赛生态
- 简历解析参考了 MinerU 与 YOLOv10 的开源工作
- Agent 设计模式参考了 LangGraph 与 ReAct 的开源实现

## 📜 开源协议

本项目基于 [GNU Affero General Public License v3.0](./LICENSE) 开源。

这意味着：
- ✅ 你可以自由使用、修改和分发本项目
- ✅ 网络服务使用也必须开源修改后的代码
- ❌ 不可闭源商用

我们选择 AGPL-3.0 是为了确保所有基于本项目的改进都能回馈社区，特别是保护部署为网络服务的场景下，修改版本必须保持开源。

## 📈 Star History

<a href="https://www.star-history.com/#WHaoxM/cpa-agent&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=WHaoxM/cpa-agent&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=WHaoxM/cpa-agent&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=WHaoxM/cpa-agent&type=Date" />
 </picture>
</a>

---

<div align="center">

**CPA-Agent** · 由四川轻化工大学团队维护

</div>
