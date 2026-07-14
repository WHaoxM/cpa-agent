# CPA-Agent Code Wiki

> 基于AI的大学生职业规划智能体 — 完整代码架构文档
> 生成日期: 2026-06-05

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [目录结构](#3-目录结构)
4. [后端架构 (Backend)](#4-后端架构-backend)
   - 4.1 [应用入口与工厂](#41-应用入口与工厂)
   - 4.2 [API 路由层](#42-api-路由层)
   - 4.3 [服务层 (Services)](#43-服务层-services)
   - 4.4 [数据处理管线 (Pipeline)](#44-数据处理管线-pipeline)
   - 4.5 [数据库客户端](#45-数据库客户端)
   - 4.6 [Agent 智能体引擎](#46-agent-智能体引擎)
   - 4.7 [工具层 (Tools)](#47-工具层-tools)
   - 4.8 [中间件与异常处理](#48-中间件与异常处理)
5. [前端架构 (Frontend)](#5-前端架构-frontend)
6. [数据存储架构](#6-数据存储架构)
7. [L0-L4 管线详解](#7-l0-l4-管线详解)
8. [依赖关系图](#8-依赖关系图)
9. [项目运行方式](#9-项目运行方式)
10. [环境配置](#10-环境配置)
11. [关键设计决策](#11-关键设计决策)

---

## 1. 项目概述

**CPA-Agent** (Career Planning Agent) 是一个面向大学生的AI职业规划智能体系统，核心能力包括：

- **简历解析**: MinerU + YOLOv10 + Qwen3-SFT 多模态简历解析
- **知识图谱**: Neo4j 技能-岗位-职业路径图谱
- **四维画像**: D1基础/D2技能/D3素养/D4潜力 四维评估
- **人岗匹配**: 多维度加权匹配 + 技能差距分析
- **报告生成**: 6章结构化职业发展报告
- **智能对话**: LangGraph/ReAct 双引擎 Agent，支持工具调用与流式输出

**技术栈**:
- 后端: Python 3.11+ / Flask / LangGraph / LangChain
- 前端: Vue 3 / TypeScript / Vite / Element Plus / ECharts / Three.js
- 数据库: MySQL 8.0 / Neo4j 5.x / Redis 7 / Milvus (Lite/Server)
- AI: OpenAI API (兼容 DeepSeek/Qwen) / BGE-M3 Embedding / MinerU

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Vue 3 前端 (vue-kecheng)                  │
│  Element Plus + ECharts + Three.js + Pinia + GSAP            │
└──────────────┬───────────────────────────────┬────────────────┘
               │ HTTP / SSE                     │
               ▼                                │
┌──────────────────────────────────────────────┐│
│           Flask 后端 (backend/app)            ││
│                                              ││
│  ┌─────────┐  ┌──────────┐  ┌─────────────┐ ││
│  │ API路由  │→│ 服务层    │→│ Agent引擎    │ ││
│  │ (10个BP) │  │ Pipeline │  │ LangGraph   │ ││
│  │         │  │ Session   │  │ ReAct       │ ││
│  └─────────┘  └──────────┘  └──────┬──────┘ ││
│                                     │        ││
│  ┌──────────────────────────────────▼──────┐ ││
│  │           Tools (12个工具)               │ ││
│  │ graph_query / db_query / semantic_search │ ││
│  │ pipeline_run / llm_reason / skill_*     │ ││
│  │ jd_cleaner / normalize_entity            │ ││
│  └─────────────────────────────────────────┘ ││
└──────────┬──────────┬──────────┬─────────────┘│
           │          │          │              │
     ┌─────▼──┐ ┌─────▼──┐ ┌────▼────┐  ┌──────▼─────┐
     │ MySQL  │ │ Neo4j  │ │  Redis  │  │   Milvus   │
     │ 8.0    │ │ 5.x    │ │  7      │  │ Lite/Server│
     └────────┘ └────────┘ └─────────┘  └────────────┘
```

---

## 3. 目录结构

```
cpa-agent/
├── backend/                    # 后端主项目
│   ├── app/
│   │   ├── __init__.py         # Flask 应用工厂 (create_app)
│   │   ├── config.py           # 配置管理 (从 .env 加载)
│   │   ├── exceptions.py       # 统一错误码与异常
│   │   ├── api/                # API 路由层 (10 个 Blueprint)
│   │   │   ├── agent.py        #   智能体对话 (核心)
│   │   │   ├── career.py       #   职业探索 (冷启动)
│   │   │   ├── pipeline.py     #   管线任务管理
│   │   │   ├── pipeline_ext.py #   管线同步调用
│   │   │   ├── profile.py      #   学生简历
│   │   │   ├── match.py        #   人岗匹配
│   │   │   ├── report.py       #   报告查询
│   │   │   ├── graph.py        #   知识图谱
│   │   │   ├── learning.py     #   学习进度
│   │   │   ├── favorites.py    #   收藏/目标岗位
│   │   │   ├── admin.py        #   管理后台
│   │   │   └── admin_kb.py     #   知识库管理
│   │   ├── services/           # 业务服务层
│   │   │   ├── langgraph_agent.py  # LangGraph Agent 引擎
│   │   │   ├── react_agent.py      # ReAct Agent 引擎
│   │   │   ├── pipeline.py         # L0-L4 管线调度
│   │   │   ├── session_manager.py  # 会话管理 (Redis)
│   │   │   ├── agent_logger.py     # Agent 日志
│   │   │   ├── embedding_service.py # BGE-M3 Embedding
│   │   │   ├── entity_normalizer.py # 实体归一化
│   │   │   ├── db/                 # 数据库客户端
│   │   │   │   ├── mysql_client.py #   MySQL (SQLAlchemy)
│   │   │   │   ├── neo4j_client.py #   Neo4j (官方 Driver)
│   │   │   │   ├── redis_client.py #   Redis
│   │   │   │   └── milvus_client.py#   Milvus 向量
│   │   │   └── tools/              # Agent 工具
│   │   │       ├── base.py         #   工具基类 BaseTool
│   │   │       ├── graph_query.py  #   图谱查询
│   │   │       ├── db_query.py     #   数据库查询
│   │   │       ├── semantic_search.py # 语义检索
│   │   │       ├── pipeline_run.py #   管线执行
│   │   │       ├── llm_reason.py   #   LLM 推理
│   │   │       ├── jd_cleaner.py   #   JD 清洗
│   │   │       └── skill_tools.py  #   技能工具集 (6个)
│   │   ├── pipeline/               # 数据处理管线
│   │   │   └── entity_extractor/   #   实体抽取子模块
│   │   │       ├── l0_parser.py    #     L0 简历解析
│   │   │       ├── l0_full_parser.py#    L0 完整解析
│   │   │       ├── nsle_pipeline.py#     NSLE 管线
│   │   │       ├── entity_pipeline.py#   实体管线
│   │   │       ├── graph_storage.py#     图谱存储
│   │   │       ├── matcher.py      #     人岗匹配器
│   │   │       ├── portrait_generator.py# 画像生成器
│   │   │       ├── report_generator.py#   报告生成器
│   │   │       ├── l4_report_generator.py# L4报告
│   │   │       ├── llm_skill_extractor.py# LLM技能抽取
│   │   │       ├── skill_matcher.py #    技能匹配
│   │   │       ├── profile_normalizer.py#  归一化
│   │   │       ├── jd_aggregator.py #    JD聚合器
│   │   │       ├── schemas.py      #     数据模式
│   │   │       ├── layout/         #     布局检测
│   │   │       │   └── yolo_layout_detector.py
│   │   │       ├── prompts/        #     Prompt 模板
│   │   │       │   ├── resume_prompts.py
│   │   │       │   └── jd_prompts.py
│   │   │       ├── rules/          #     规则引擎
│   │   │       │   ├── logic_rules.py
│   │   │       │   └── privacy_rules.py
│   │   │       └── dictionaries/    #     字典/词表
│   │   │           ├── skill_lexicon.py
│   │   │           ├── entity_patterns.py
│   │   │           └── pii_patterns.py
│   │   ├── models/             # 数据模型 (SQLAlchemy)
│   │   ├── schemas/            # Pydantic Schema
│   │   ├── middleware/          # 中间件
│   │   │   ├── error_handler.py
│   │   │   ├── cors_middleware.py
│   │   │   └── logging_middleware.py
│   │   ├── validators/         # 输入校验
│   │   └── utils/               # 工具函数
│   │       ├── llm_client.py    #   LLM 客户端
│   │       ├── logger.py        #   日志
│   │       └── retry.py         #   重试
│   ├── scripts/                 # 初始化与种子脚本
│   │   ├── init_mysql.sql       #   MySQL 建表
│   │   ├── init_neo4j.cql       #   Neo4j 建图
│   │   ├── init_milvus.py       #   Milvus 建集合
│   │   ├── seed_cold_start_*.sql#   冷启动数据
│   │   ├── seed_neo4j_*.cypher  #   图谱种子数据
│   │   └── seed_skill_vectors.py#   技能向量种子
│   ├── run.py                   # 启动入口
│   ├── requirements.txt         # Python 依赖
│   ├── environment.yml          # Conda 环境
│   └── Dockerfile
│
├── vue-kecheng/                # 前端主项目
│   ├── src/
│   │   ├── api/                 # API 接口层
│   │   ├── assets/              # 静态资源
│   │   │   ├── styles/          #   主题与样式
│   │   │   ├── data/            #   地理JSON数据
│   │   │   └── images/          #   图片
│   │   ├── components/          # 组件
│   │   │   ├── charts/          #   D3图表 (雷达/弦图/矩形树图)
│   │   │   ├── book/            #   古籍风格UI
│   │   │   ├── career/          #   职业模块
│   │   │   └── bookshelf/       #   3D书架
│   │   ├── composables/         # 组合式函数 (9个)
│   │   ├── constants/           # 常量
│   │   ├── layouts/             # 布局
│   │   ├── mock/                # Mock数据 (5个文件)
│   │   ├── plugins/             # GSAP插件
│   │   ├── router/              # 路由
│   │   ├── stores/              # Pinia状态管理 (8个)
│   │   ├── types/               # TypeScript类型 (8个领域)
│   │   ├── utils/               # 工具函数
│   │   ├── views/               # 页面视图
│   │   ├── App.vue
│   │   └── main.ts
│   ├── package.json
│   └── vite.config.ts
│
├── archive/                    # 归档目录 (早期原型/第三方项目/历史交付物)
│   ├── third-party/            #   第三方项目 (MiroFish 已归档于此)
│   ├── early-prototypes/       #   早期原型 (mvp/data/knowledge_base 已归档于此)
│   └── ...
│
├── models/                     # 本地模型占位目录
│   ├── embeddings/             #   Embedding 模型
│   ├── llm/                    #   LLM 模型
│   ├── mineru/                 #   MinerU 模型
│   └── yolo/                   #   YOLO 模型
│
├── docs/                       # 文档
├── .trae/rules/                # 项目规则 (数据库契约)
└── docker-compose.yml          # Docker 编排
```

---

## 4. 后端架构 (Backend)

### 4.1 应用入口与工厂

| 文件 | 职责 |
|------|------|
| [run.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/run.py) | 启动入口，设置环境变量、编码、调用 `create_app()` |
| [app/__init__.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/__init__.py) | Flask 应用工厂 `create_app()`，注册 Blueprint、中间件 |
| [app/config.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/config.py) | 配置类 `Config`，从 `.env` 加载所有配置 |

**启动流程**:
```
run.py → Config.validate() → create_app() → 注册 Blueprint → app.run()
```

**Blueprint 注册表**:

| Blueprint | URL 前缀 | 职责 |
|-----------|---------|------|
| `graph_bp` | `/api/graph` | 知识图谱查询 |
| `profile_bp` | `/api/profile` | 学生简历管理 |
| `match_bp` | `/api/match` | 人岗匹配 |
| `report_bp` | `/api/report` | 报告查询 |
| `agent_bp` | `/api/agent` | 智能体对话 (核心) |
| `pipeline_bp` | `/api/pipeline` | 管线任务管理 |
| `pipeline_ext_bp` | `/api/pipeline/ext` | 管线同步调用 |
| `learning_bp` | `/api/learning` | 学习进度 |
| `admin_bp` | `/api/admin` | 管理后台 |
| `favorites_bp` | `/api` | 收藏/目标岗位 |
| `career_bp` | `/api/career` | 职业探索 (冷启动) |

### 4.2 API 路由层

#### 核心 API: Agent 对话

[agent.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/agent.py) 是系统最核心的路由：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/agent/chat` | POST | 智能体对话 (支持 LangGraph + ReAct 双引擎) |
| `/api/agent/chat/stream` | POST | SSE 流式对话 (仅 LangGraph) |
| `/api/agent/session/create` | POST | 创建会话 |
| `/api/agent/session/<id>/history` | GET | 获取会话历史 |
| `/api/agent/session/<id>` | DELETE | 删除会话 |
| `/api/agent/session/<id>/context` | GET/PUT | 获取/更新会话上下文 |
| `/api/agent/sessions` | GET | 列出会话 |
| `/api/agent/stats` | GET | Agent 工具执行统计 |

**对话引擎切换**: 请求参数 `mode=langgraph`(默认) 或 `mode=react`

#### 职业 API: 冷启动探索

[career.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/career.py) 提供冷启动用户无需简历即可探索职业方向：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/career/landscape` | GET | 职业全景图 |
| `/api/career/insights` | GET | 方向洞察列表 |
| `/api/career/insights/<role>` | GET | 单方向详情 |
| `/api/career/skill-tree` | GET | 技能树 |
| `/api/career/learning-path` | GET | 学习路径 |
| `/api/career/explore` | GET | 语义探索 (Milvus) |
| `/api/career/paths` | GET | 晋升路径 (Neo4j) |
| `/api/career/transfer` | GET | 换岗路径 (Neo4j) |
| `/api/career/assessment` | POST | 提交技能自评 |

#### 管线 API

[pipeline.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/api/pipeline.py) 提供异步任务模式：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/pipeline/list` | GET | 列出所有管线 |
| `/api/pipeline/trigger` | POST | 触发管线任务 |
| `/api/pipeline/status/<task_id>` | GET | 查询任务状态 |
| `/api/pipeline/l0/parse-resume` | POST | L0 简历解析 |
| `/api/pipeline/l1/build-graph` | POST | L1 图谱构建 |
| `/api/pipeline/l2/generate-portrait` | POST | L2 画像生成 |
| `/api/pipeline/l3/run-match` | POST | L3 人岗匹配 |
| `/api/pipeline/l4/generate-report` | POST | L4 报告生成 |

### 4.3 服务层 (Services)

| 模块 | 文件 | 职责 |
|------|------|------|
| LangGraph Agent | [langgraph_agent.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/langgraph_agent.py) | LangGraph StateGraph 编排，ToolNode + function_calling |
| ReAct Agent | [react_agent.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/react_agent.py) | 手写 ReAct 循环，正则解析工具调用 |
| Pipeline | [pipeline.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/pipeline.py) | L0-L4 管线调度器 |
| Session Manager | [session_manager.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/session_manager.py) | Redis 会话管理 |
| Embedding | [embedding_service.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/embedding_service.py) | BGE-M3 向量化 (Singleton) |
| Entity Normalizer | [entity_normalizer.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/entity_normalizer.py) | 实体名归一化 |
| Agent Logger | [agent_logger.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/agent_logger.py) | Agent 行为日志 |

### 4.4 数据处理管线 (Pipeline)

核心管线调度器位于 [services/pipeline.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/pipeline.py)，实体抽取子模块位于 `pipeline/entity_extractor/`：

| 模块 | 职责 |
|------|------|
| `l0_parser.py` | L0 简历解析 (MinerU + YOLOv10 + Qwen3-SFT) |
| `l0_full_parser.py` | L0 完整解析流程 |
| `nsle_pipeline.py` | NSLE (Named-entity + Symbolic + Logic + Embedding) 管线 |
| `entity_pipeline.py` | 实体抽取管线 |
| `graph_storage.py` | Neo4j 图谱存储 |
| `matcher.py` | 人岗匹配器 (calculate_match_score) |
| `portrait_generator.py` | 四维画像生成器 |
| `report_generator.py` | 报告生成器 |
| `l4_report_generator.py` | L4 报告生成 |
| `llm_skill_extractor.py` | LLM 增强技能抽取 |
| `skill_matcher.py` | 技能匹配 |
| `profile_normalizer.py` | 实体归一化 |
| `jd_aggregator.py` | JD 聚合器 |
| `layout/yolo_layout_detector.py` | YOLOv10 布局检测 |
| `prompts/resume_prompts.py` | 简历 Prompt 模板 |
| `prompts/jd_prompts.py` | JD Prompt 模板 |
| `rules/logic_rules.py` | 逻辑规则引擎 |
| `rules/privacy_rules.py` | 隐私规则引擎 |
| `dictionaries/skill_lexicon.py` | 技能词典 |
| `dictionaries/entity_patterns.py` | 实体模式 |
| `dictionaries/pii_patterns.py` | PII 脱敏模式 |

### 4.5 数据库客户端

所有数据库客户端均为**线程安全单例**模式：

| 客户端 | 文件 | 连接方式 |
|--------|------|---------|
| `MySQLClient` | [mysql_client.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/db/mysql_client.py) | SQLAlchemy QueuePool (10+20) |
| `Neo4jClient` | [neo4j_client.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/db/neo4j_client.py) | 官方 Driver (pool_size=50) |
| `RedisClient` | [redis_client.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/db/redis_client.py) | redis-py |
| `MilvusClient` | [milvus_client.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/db/milvus_client.py) | pymilvus (Lite/Server 双模式) |

**MySQLClient 关键方法**:
- `execute(sql, params, commit)` — 通用 SQL 执行
- `fetch_all(sql, params)` — 查询全部
- `execute_write(sql, params)` — 写操作 (自动提交)
- `execute_many(sql, params)` — 批量执行
- `ALLOWED_TABLES` — 白名单表名校验

**MilvusClient 双模式**:
- Lite 模式: `uri` 以 `.db` 结尾，嵌入式本地文件
- Server 模式: `uri` 为 `host:port`，连接独立 Milvus 服务

### 4.6 Agent 智能体引擎

系统提供**双引擎**架构：

#### LangGraph Agent (主引擎)

[langgraph_agent.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/langgraph_agent.py)

```
┌──────────────────────────────────────────────────┐
│                  StateGraph                       │
│                                                   │
│  ┌─────────┐    tools_condition    ┌──────────┐  │
│  │  agent  │ ───────────────────→ │  tools   │  │
│  │ (LLM)  │ ←─────────────────── │(ToolNode)│  │
│  └─────────┘                      └──────────┘  │
│       │                              │            │
│       │ (无 tool_calls)              │            │
│       ▼                              │            │
│      END  ←─────────────────────────┘            │
│                                                   │
│  State: { messages, student_context }             │
│  Checkpointer: PyMySQLSaver (MySQL持久化)         │
└──────────────────────────────────────────────────┘
```

**核心类**: `LangGraphAgentService`
- `chat()` — 同步对话
- `chat_stream()` — SSE 流式对话
- `_select_subagent()` — 子Agent路由 (planner/profile/match/report/qa)
- `get_checkpointer()` — MySQL Checkpointer 单例

**工具适配**: `_adapt_existing_tool()` 将 `BaseTool` 子类适配为 LangChain `@tool`

#### ReAct Agent (Fallback)

[react_agent.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/react_agent.py)

```
用户消息 → System Prompt (含工具描述)
         → LLM 生成回复
         → 解析: 工具调用? / Final Answer? / 直接回复?
         → 工具调用 → 执行 → Observation → 继续循环
         → Final Answer → 返回
```

**核心类**: `ReactAgent`
- `chat()` — ReAct 循环
- `_parse_tool_calls()` — 解析工具调用 (XML标签 + 裸JSON + 尾部JSON)
- `_execute_tool()` — 执行工具
- 冲突处理: 同时输出工具调用和 Final Answer 时的重试逻辑

### 4.7 工具层 (Tools)

所有工具继承自 [BaseTool](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/tools/base.py)：

```python
class BaseTool(ABC):
    name: str          # 工具名称
    description: str   # 工具描述
    parameters: Dict   # 参数定义
    execute(**kwargs) -> str  # 执行方法
```

| 工具 | 文件 | 说明 |
|------|------|------|
| `graph_query` | [graph_query.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/tools/graph_query.py) | Neo4j 图谱查询 |
| `db_query` | [db_query.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/tools/db_query.py) | MySQL 数据查询 |
| `semantic_search` | [semantic_search.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/tools/semantic_search.py) | Milvus 语义检索 |
| `pipeline_run` | [pipeline_run.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/tools/pipeline_run.py) | 管线执行 |
| `llm_reason` | [llm_reason.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/tools/llm_reason.py) | LLM 深度推理 |
| `jd_cleaner` | [jd_cleaner.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/tools/jd_cleaner.py) | JD 清洗 |
| `skill_extract` | [skill_tools.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/services/tools/skill_tools.py) | 技能抽取 |
| `skill_match` | skill_tools.py | 人岗匹配 |
| `portrait_generate` | skill_tools.py | 画像生成 |
| `report_generate` | skill_tools.py | 报告生成 |
| `skill_recommend` | skill_tools.py | 技能推荐 |
| `learning_path` | skill_tools.py | 学习路径 |
| `normalize_entity` | langgraph_agent.py | 实体归一化 (原生 @tool) |

### 4.8 中间件与异常处理

| 中间件 | 文件 | 职责 |
|--------|------|------|
| `CorsMiddleware` | [cors_middleware.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/middleware/cors_middleware.py) | CORS 跨域 |
| `ErrorHandler` | [error_handler.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/middleware/error_handler.py) | 统一错误响应 |
| `LoggingMiddleware` | [logging_middleware.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/middleware/logging_middleware.py) | 请求日志 |

**异常体系** ([exceptions.py](file:///d:/AGeneral%20Workspace/cpa-agent/backend/app/exceptions.py)):

```
ErrorCode (Enum)
  ├── PARAM_INVALID / NOT_FOUND
  ├── DB_ERROR / MYSQL_ERROR / NEO4J_ERROR / REDIS_ERROR / MILVUS_ERROR
  ├── LLM_ERROR / TOOL_ERROR / PIPELINE_ERROR
  ├── AUTH_ERROR / PERMISSION_ERROR / RATE_LIMIT_ERROR
  └── INTERNAL_ERROR / SERVICE_UNAVAILABLE

APIException (Exception)
  ├── error_code: ErrorCode
  ├── message: str
  ├── details: Any
  └── status_code: int
```

---

## 5. 前端架构 (Frontend)

**项目**: `vue-kecheng/`

| 技术栈 | 版本 | 用途 |
|--------|------|------|
| Vue | 3.5+ | 核心框架 |
| TypeScript | 5.9 | 类型系统 |
| Vite | 7.3 | 构建工具 |
| Element Plus | 2.13 | UI 组件库 |
| Pinia | 3.0 | 状态管理 |
| ECharts | 5.6 | 图表 |
| D3.js | 7.9 | 自定义图表 (雷达/弦图/矩形树图) |
| Three.js | 0.183 | 3D 可视化 |
| GSAP | 3.14 | 动画引擎 |
| TipTap | 3.21 | 富文本编辑 |

**Pinia Stores**:

| Store | 文件 | 职责 |
|-------|------|------|
| `useUserStore` | user.ts | 用户登录态、角色 |
| `useThemeStore` | theme.ts | 主题切换 |
| `useResumeStore` | resume.ts | 简历数据 |
| `useReportStore` | report.ts | 报告数据 |
| `useCourseStore` | course.ts | 课程数据 |
| `useLearningStore` | learning.ts | 学习进度 |

**TypeScript 类型领域**:

| 文件 | 领域 |
|------|------|
| `user.ts` | 用户、角色、登录 |
| `course.ts` | 课程、章节 |
| `career.ts` | 职业、画像、匹配 |
| `chart.ts` | 图表数据 |
| `knowledge.ts` | 知识图谱 |
| `report.ts` | 报告 |
| `ai.ts` | AI 对话 |
| `api.ts` | API 响应 |

**三角色体系**: `student` / `teacher` / `admin`，路由守卫在 `router/index.ts` 实现

---

## 6. 数据存储架构

### 6.1 MySQL (关系型 — 核心业务数据)

**15 张核心表** (Schema 冻结 v1.0):

| 表 | 用途 | 关键字段 |
|----|------|---------|
| `student_profile` | 学生简历 (L0) | student_id, profile_json, degree, school |
| `job_posting` | 岗位信息 (L0) | job_id, position_name, company_name |
| `student_portrait_4d` | 学生四维画像 (L2) | d1-d4_score, competitiveness, skill_coverage |
| `job_portrait_4d` | 岗位四维画像 (L2) | portrait_id, position_name, d1-d4_weight |
| `match_result` | 匹配结果 (L3) | total_score, is_matched, skill_coverage |
| `skill_gap` | 技能差距 (L3) | critical_count, gap_json |
| `career_path` | 职业路径 (L3) | recommended_dir, path_json |
| `career_report` | 职业报告 (L4) | ch1-ch6_json, status, report_json |
| `conversation` | 对话会话 | session_id, student_id |
| `conversation_message` | 对话消息 | role, content, tool_calls_json |
| `pipeline_task` | 管线任务 | pipeline, status, progress |
| `saved_job` | 收藏岗位 | user_id, job_id |
| `target_role` | 目标岗位 | user_id, role_name |
| `course` / `chapter` | 课程体系 | course_id, category |
| `learning_progress` | 学习进度 | user_id, progress |

### 6.2 Neo4j (图数据库 — 知识图谱)

**Graph A: 技能与职业知识图谱**

| 节点 | 数量 | 属性 |
|------|------|------|
| `Skill` | ~500 | name, category, frequency, embedding |
| `SkillCategory` | 30 | name, level, skill_count |
| `JobPortrait` | ≥10 | portrait_id, position_name, d1-d4_weight |
| `CareerNode` | 30-50 | position_name, level, family |

**关系类型**: BELONGS_TO, CHILD_OF, REQUIRES, PREFERS, BONUS, PROMOTION, TRANSFER, PREREQUISITE, REPLACES, EVOLVES_TO

**Graph B: 简历实体关系网络**

19 种实体节点 + 24 种关系，每份简历的实体关系入图。

### 6.3 Milvus (向量数据库 — 语义检索)

| Collection | 维度 | 用途 |
|------------|------|------|
| `skill_vectors` | 1024 | 技能语义匹配、OOV归类 |
| `jd_vectors` | 1024 | 岗位语义检索 |
| `resume_vectors` | 1024 | 简历语义检索 |
| `conversation_vectors` | 1024 | 跨会话记忆检索 |

**Embedding**: BGE-M3 (BAAI/bge-m3), 1024维, COSINE距离, IVF_FLAT索引

### 6.4 Redis (缓存 — 会话与热数据)

| Key 模式 | TTL | 用途 |
|----------|-----|------|
| `session:{id}` | 7天 | 会话状态 |
| `cache:career:*` | 5-30min | 职业数据缓存 |
| `cache:career:explore:*` | 10min | 语义探索缓存 |

---

## 7. L0-L4 管线详解

```
L0_parse_resume ──→ L1_build_graph ──→ L2_generate_portrait ──→ L3_run_match ──→ L4_generate_report
  简历解析            知识图谱构建         四维画像生成           人岗匹配           报告生成
```

| 层级 | 类 | 输入 | 输出 | 核心逻辑 |
|------|-----|------|------|---------|
| **L0** | `L0ParseResumePipeline` | PDF/文本 | `student_profile` | MinerU解析 + YOLOv10布局 + Qwen3-SFT NER + 归一化 |
| **L1** | `L1BuildGraphPipeline` | JD文本/简历文本 | Neo4j节点/关系 | NSLE管线抽取技能 → 写入图谱 |
| **L2** | `L2GeneratePortraitPipeline` | student_id + skills | `student_portrait_4d` | PortraitGenerator 四维评分 |
| **L3** | `L3RunMatchPipeline` | student_id + job_portrait_id | `match_result` + `skill_gap` + `career_path` | SkillMatcher + 晋升/换岗路径 |
| **L4** | `L4GenerateReportPipeline` | student_id + job_portrait_id | `career_report` | ReportGenerator 6章报告 |

**管线调度器**: `PipelineRunner` — 统一入口，创建 `pipeline_task` 记录，分发到具体管线类

---

## 8. 依赖关系图

### 后端核心依赖链

```
Flask App
  ├── flask-cors (CORS)
  ├── langgraph (Agent编排)
  │   ├── langchain-openai (LLM)
  │   ├── langchain-core (基础)
  │   └── langgraph-checkpoint-mysql (持久化)
  ├── openai (LLM API)
  ├── sqlalchemy + pymysql (MySQL)
  ├── neo4j (图数据库)
  ├── redis (缓存)
  ├── pymilvus (向量数据库)
  ├── sentence-transformers (BGE-M3)
  ├── pydantic (数据校验)
  ├── jinja2 (报告模板)
  └── python-dotenv (配置)
```

### 模块间调用关系

```
API 路由 (agent.py)
  ├──→ SessionManager (Redis) — 会话管理
  ├──→ LangGraphAgentService — 主引擎
  │     ├──→ ChatOpenAI — LLM调用
  │     ├──→ ToolNode — 工具执行
  │     │     ├──→ GraphQueryTool → Neo4jClient
  │     │     ├──→ DbQueryTool → MySQLClient
  │     │     ├──→ SemanticSearchTool → MilvusClient + EmbeddingService
  │     │     ├──→ PipelineRunTool → PipelineRunner
  │     │     ├──→ LlmReasonTool → LLMClient
  │     │     └──→ SkillTools → Pipeline子模块
  │     └──→ PyMySQLSaver — Checkpoint持久化
  └──→ ReactAgent — Fallback引擎
        └──→ 同上工具集 (BaseTool)
```

---

## 9. 项目运行方式

### 方式一: 本地开发 (推荐)

**环境要求**: Python 3.11+, Node.js 20.19+, Conda

```powershell
# 1. 创建 Conda 环境
conda create -n cpa-agent python=3.11
conda activate cpa-agent

# 2. 安装后端依赖
cd backend
pip install -r requirements.txt

# 3. 配置 .env
copy .env.example .env
# 编辑 .env 填入 LLM_API_KEY, MYSQL_PASSWORD 等

# 4. 初始化数据库
python scripts/mysql/init_mysql.py
python scripts/neo4j/init_neo4j.py
python scripts/milvus/init_milvus.py

# 5. 启动后端
python run.py
# → http://localhost:5001

# 6. 启动前端
cd ../vue-kecheng
npm install
npm run dev
# → http://localhost:5173
```

### 方式二: Docker Compose

```powershell
# 一键启动所有服务
docker-compose up -d

# 包含: backend:5001, mysql:3306, neo4j:17687, redis:6379
```

### 关键端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Flask Backend | 5001 | API 服务 |
| MySQL | 3306 | 关系数据库 |
| Neo4j | 17687 (bolt) / 17668 (http) | 图数据库 |
| Redis | 6379 | 缓存 |
| Milvus | 19530 | 向量数据库 (Server模式) |
| Vue Frontend | 5173 | 前端开发服务器 |

---

## 10. 环境配置

### 核心环境变量 (.env)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `openai` | LLM 提供商 (openai/deepseek/qwen/kimi/zhipu/ollama/vllm/lmstudio/custom) |
| `LLM_API_KEY` | — | API Key (必填) |
| `LLM_BASE_URL` | 按provider | API Base URL |
| `LLM_MODEL_NAME` | 按provider | 模型名称 |
| `LLM_TIMEOUT` | `120` | 超时秒数 |
| `MYSQL_HOST` | `localhost` | MySQL 主机 |
| `MYSQL_PASSWORD` | — | MySQL 密码 |
| `MYSQL_DATABASE` | `cpa_agent` | 数据库名 |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j URI |
| `NEO4J_PASSWORD` | — | Neo4j 密码 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL |
| `MILVUS_URI` | `localhost:19530` | Milvus URI (Lite模式用 `.db` 路径) |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding 模型 |
| `EMBEDDING_DIM` | `1024` | 向量维度 |
| `AGENT_MAX_TOOL_CALLS` | `5` | Agent 最大工具调用次数 |
| `AGENT_MAX_ITERATIONS` | `8` | Agent 最大迭代次数 |
| `FLASK_DEBUG` | `True` | 调试模式 |
| `FLASK_PORT` | `5001` | 服务端口 |

### LLM 提供商预设

| Provider | Base URL | 默认模型 |
|----------|----------|---------|
| `openai` | api.openai.com/v1 | gpt-4o-mini |
| `deepseek` | api.deepseek.com/v1 | deepseek-chat |
| `qwen` | dashscope.aliyuncs.com/compatible-mode/v1 | qwen-plus |
| `kimi` | api.moonshot.cn/v1 | moonshot-v1-8k |
| `zhipu` | open.bigmodel.cn/api/paas/v4 | glm-4 |
| `ollama` | localhost:11434/v1 | qwen2.5:latest |
| `vllm` | localhost:8000/v1 | Qwen/Qwen2.5-7B-Instruct |

---

## 11. 关键设计决策

### 11.1 双引擎 Agent

- **LangGraph** (主): 原生 function_calling，结构化工具调用，MySQL Checkpoint 持久化，SSE 流式
- **ReAct** (备): 手写循环，正则解析，兼容不支持 function_calling 的 LLM

### 11.2 Hybrid 存储模式

- **MySQL**: 高频查询字段拆列 + 完整 JSON 保留原始结构
- **Neo4j**: Graph A (技能职业图谱) + Graph B (简历实体关系) 分离
- **Milvus**: 4 个 Collection 按语义检索场景分离
- **Redis**: 会话状态 + 热数据缓存

### 11.3 Schema 冻结

MySQL/Neo4j/Milvus 的 Schema 已冻结 (v1.0, 2026-04-17)，仅允许新增，禁止修改/删除。

### 11.4 四维画像模型

```
竞争力 = D1×0.25 + D2×0.35 + D3×0.20 + D4×0.20

D1 基础要求: 学历/专业/证书等硬约束
D2 职业技能: 技能覆盖率、技能水平
D3 职业素养: 软技能、项目经验
D4 发展潜力: 竞赛/论文/创新
```

### 11.5 前端优先设计

数据库字段设计以前端 API 需求为导向，后端字段向前端接口靠拢。

### 11.6 管线分层

L0→L4 逐层递进，每层独立可执行，通过 `PipelineRunner` 统一调度，`pipeline_task` 表追踪状态。
