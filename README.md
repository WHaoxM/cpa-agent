<div align="center">

# 职导星图 · CPA-Agent

**面向大学生全周期职业规划的多智能体决策平台**

</br>
<em>An Open-Source Multi-Agent Career Planning Platform for University Students</em>

[![License](https://img.shields.io/badge/License-AGPL--3.0-blue?style=flat-square&logo=gnu&logoColor=white)](./LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/WHaoxM/cpa-agent?style=flat-square&color=DAA520)](https://github.com/WHaoxM/cpa-agent/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/WHaoxM/cpa-agent?style=flat-square)](https://github.com/WHaoxM/cpa-agent/network)
[![GitHub Issues](https://img.shields.io/github/issues/WHaoxM/cpa-agent?style=flat-square)](https://github.com/WHaoxM/cpa-agent/issues)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3.5-42b883?style=flat-square&logo=vue.js&logoColor=white)](https://vuejs.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://www.docker.com/)

[![Live Demo](https://img.shields.io/badge/Live%20Demo-GitHub%20Pages-6E40C9?style=flat-square&logo=github&logoColor=white)](https://whaoxm.github.io/cpa-xingtu/)
[![Frontend](https://img.shields.io/badge/Frontend-cpa--xingtu-42b883?style=flat-square&logo=vue.js&logoColor=white)](https://github.com/WHaoxM/cpa-xingtu)

[中文](./README.md) | [English](./README.en.md)

</div>

## 📸 系统截图

<div align="center">
<table>
<tr>
<td><img src="./docs/screenshots/career-analysis.png" alt="职业分析" width="100%"/></td>
<td><img src="./docs/screenshots/career-ability.png" alt="能力图谱" width="100%"/></td>
</tr>
<tr>
<td><img src="./docs/screenshots/career-report.png" alt="职业报告" width="100%"/></td>
<td><img src="./docs/screenshots/talent-portrait.png" alt="人才画像" width="100%"/></td>
</tr>
</table>
</div>

> 截图为前端演示效果，实际界面以 [在线 Demo](https://whaoxm.github.io/cpa-xingtu/) 为准。

## ⚡ 项目概述

**职导星图（CPA-Agent）** 是一个面向大学生的多智能体职业规划决策平台——不是单一的岗位推荐页，而是从简历解析到职业报告生成的**完整决策闭环**。

它将复杂版式简历解析、七维能力画像、岗位需求建模、职业知识图谱、向量检索、检索增强生成与多智能体编排结合在一起，帮助学生在「诊断 → 匹配 → 学习 → 报告」全链路中获得数据驱动的职业指导。

> 你只需：上传一份 PDF 简历，或在对话中描述你的背景与目标
> CPA-Agent 将返回：结构化的四维能力画像、可解释的人岗匹配结果、可执行的职业发展报告

### 🎯 我们的愿景

职导星图致力于打破传统职业指导的局限，让每一个"如果"都能看见结果：

- **于学生**：基于真实简历与岗位数据的个性化诊断，而非泛泛的职业鸡汤
- **于高校**：批量解析、标准化画像与报告生成，适合就业指导中心规模化使用
- **于研究**：L0–L4 管线、双引擎 Agent、NSLE 实体抽取均有清晰模块边界，便于论文实验与消融

从简历输入到职业报告，每一步都有 Agent Trace 可观测，推荐结果可追溯到图谱节点与匹配权重，而不是黑盒式的一句"建议你去做产品经理"。

## 🌐 在线演示

体验前端在线 Demo（主要使用 Mock 数据，无需后端）：

👉 **[https://whaoxm.github.io/cpa-xingtu/](https://whaoxm.github.io/cpa-xingtu/)**

**演示账号：**

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 学生 | 123456 | 123456 |
| 管理员 | 123456 | 123456 |

## ✨ 核心亮点

两个能力决定了 CPA-Agent 与通用 Chatbot 的本质差异：

- 🧭 **L0–L4 分层管线。** 简历解析、图谱构建、画像生成、人岗匹配、报告生成各自独立、又可串联调度。每层有明确的输入输出与 `pipeline_task` 状态追踪，适合高校场景下的批处理与异步任务。
- 🤖 **双引擎 Agent + 12 工具。** LangGraph（主）原生 function calling + MySQL Checkpoint 持久化；ReAct（备）兼容不支持 tool call 的 LLM。Agent 可调用图谱查询、语义检索、管线触发等工具，在推理与确定性业务逻辑之间保持边界清晰。

## 🔄 工作流程

1. **简历解析（L0）**：PDF/Docx → 版面检测 → NER 实体抽取 → `student_profile`
2. **图谱构建（L1）**：简历/JD 实体 → Neo4j 节点与关系 → 技能-岗位-职业路径网络
3. **画像生成（L2）**：学生 ID + 技能 → 四维评分（D1 基础/D2 技能/D3 素养/D4 潜力）→ `student_portrait_4d`
4. **人岗匹配（L3）**：学生画像 + 岗位画像 → 加权匹配分 + 技能差距 + 晋升路径
5. **报告生成（L4）**：学生 + 目标岗位 → 六章 `career_report`（含学习路径与评估周期）

## 🚀 快速开始

### 一、在线体验（推荐首次使用）

直接访问 [在线 Demo](https://whaoxm.github.io/cpa-xingtu/)，使用演示账号登录即可体验全部前端功能。

### 二、本地部署（完整功能）

#### 前置要求

| 工具 | 版本 | 说明 | 安装检查 |
|------|------|------|---------|
| **Python** | 3.11+ | 后端运行环境 | `python --version` |
| **Node.js** | ^20.19.0 \|\| >=22.12.0 | 前端运行环境 | `node -v` |
| **Conda** | 推荐 | 环境管理 | `conda --version` |
| **Docker** | 最新 | 数据库依赖 | `docker --version` |

#### 1. 配置环境变量

```bash
# 复制示例配置文件
cp .env.example .env
# Windows PowerShell:
# Copy-Item .env.example .env

# 编辑 .env 文件，填入必要的 API 密钥与数据库密码
```

**必需的环境变量：**

```env
# LLM API 配置（支持 OpenAI SDK 格式的任意 LLM API）
# 推荐：DeepSeek / Qwen / Kimi / Ollama
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-chat

# 数据库配置
MYSQL_PASSWORD=your_password
NEO4J_PASSWORD=your_password
REDIS_URL=redis://localhost:6379/0

# 向量数据库（默认使用 Lite 嵌入式模式，无需额外部署）
MILVUS_URI=./milvus_data/milvus.db
EMBEDDING_MODEL=BAAI/bge-m3
```

#### 2. 启动数据库依赖

```bash
# 启动 MySQL + Neo4j + Redis
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d mysql neo4j redis
```

#### 3. 启动后端

```bash
conda activate mkg  # 或你的环境名
cd backend
pip install -r requirements.txt

# 初始化数据库与图谱
python scripts/neo4j/init_neo4j.py
python scripts/milvus/init_milvus.py

# 启动服务
python run.py
# → http://localhost:5001
```

#### 4. 启动前端

前端为独立仓库 [cpa-xingtu](https://github.com/WHaoxM/cpa-xingtu)：

```bash
git clone https://github.com/WHaoxM/cpa-xingtu.git
cd cpa-xingtu
npm install
Copy-Item .env.example .env.local
# .env.local 中 VITE_API_BASE_URL=http://127.0.0.1:5001
npm run dev
# → http://localhost:5173
```

### 三、Docker 部署（数据库层）

```bash
# 开发环境（仅数据库）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# 生产环境（含后端）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**端口映射：**
- 前端：`5173`（开发）/ `3000`（Docker）
- 后端 API：`5001`
- MySQL：`13306`
- Neo4j Bolt：`17687` / HTTP：`17668`
- Redis：`16379`

## 🏗️ 架构概览

```
Vue 3 前端 (cpa-xingtu)
        │ HTTP / SSE
        ▼
Flask 后端 (backend/app)
  API 路由 → 服务层 → Agent 引擎 (LangGraph / ReAct)
                        │
                        ▼
              Tools (12) ──→ MySQL / Neo4j / Redis / Milvus
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

- **Python 3.11+** / Flask 3.x
- **LangGraph + LangChain** - Agent 编排（主引擎）
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

- **后端** Flask + LangGraph / LangChain，Python 3.11+，默认端口 `5001`
- **前端** Vue 3 + TypeScript + Element Plus + D3 / ECharts / Three.js，开发端口 `5173`
- **数据层** MySQL 8.0 · Neo4j 5.x · Redis 7 · Milvus Lite / Server
- **简历解析** MinerU + YOLOv10 布局检测 + Qwen3-SFT NER + 实体归一化
- **Agent 工具** 图谱查询、DB 查询、语义检索、管线触发、LLM 推理、JD 清洗、技能工具集等 12 个
- **输出模式** 四维画像、人岗匹配、技能差距、六章职业发展报告、SSE 流式对话
- **部署** Dev Docker Compose 只拉起 MySQL + Neo4j + Redis；后端本机运行，前端可独立部署至 GitHub Pages

## 💡 一个具体例子

学生上传简历，并对 AI 助手说：

> 我是计算机专业大三学生，做过两个 Spring Boot 后端项目，熟悉 MySQL 和 Redis，想毕业后做后端开发，但不太确定该冲大厂还是先去创业公司积累经验。

系统经过管线与 Agent 协同后，可能返回：

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
- **研究复现** — L0–L4 管线、NSLE 实体抽取、双引擎 Agent 均有清晰模块边界，便于论文实验与消融

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
| [CODE_WIKI.md](./CODE_WIKI.md) | 完整代码架构与运行指南 |
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
