# CPA-Agent 开发日志

---

## 2026-04-05: 后端骨架搭建 (参考 MiroFish)

### 做了什么

深入分析了 MiroFish 开源项目 (GitHub 666ghj/MiroFish, 3.5k+ stars), 提取可复用的架构模式, 搭建 CPA-Agent 后端骨架。

### 新增文件 (25个)

```
backend/
├── run.py                              # 启动入口 (Windows UTF-8 兼容)
├── requirements.txt                    # 依赖清单
├── .env.example                        # 环境变量模板
├── app/
│   ├── __init__.py                     # Flask 应用工厂 (create_app)
│   ├── config.py                       # 配置管理 (四库连接 + Agent 参数)
│   ├── api/                            # 5 个 Blueprint 路由
│   │   ├── __init__.py                 # Blueprint 注册
│   │   ├── graph.py                    # /api/graph — Neo4j 图谱
│   │   ├── profile.py                  # /api/profile — 学生/岗位画像
│   │   ├── match.py                    # /api/match — 匹配/差距分析
│   │   ├── report.py                   # /api/report — 报告生成/导出
│   │   └── agent.py                    # /api/agent — 智能体对话
│   ├── services/                       # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── react_agent.py              # ReAct Agent 核心 (~320行)
│   │   ├── agent_logger.py             # JSONL 结构化日志
│   │   └── tools/                      # 5 个 Tool 实现
│   │       ├── __init__.py
│   │       ├── base.py                 # BaseTool 抽象基类
│   │       ├── graph_query.py          # Tool 1: Neo4j Text-to-Cypher
│   │       ├── db_query.py             # Tool 2: MySQL 业务查询
│   │       ├── semantic_search.py      # Tool 3: Milvus 语义检索
│   │       ├── pipeline_run.py         # Tool 4: L0-L4 管线触发
│   │       └── llm_reason.py           # Tool 5: LLM 推理/建议
│   ├── models/
│   │   └── __init__.py
│   └── utils/                          # 工具库
│       ├── __init__.py
│       ├── llm_client.py               # LLM 统一封装 (OpenAI SDK)
│       ├── logger.py                   # 日志 (控制台 + 文件轮转)
│       └── retry.py                    # 指数退避重试装饰器
```

### 参考来源

- MiroFish `report_agent.py` → `react_agent.py` (ReAct 循环 + Tool Call 解析)
- MiroFish `llm_client.py` → `utils/llm_client.py` (OpenAI SDK 封装)
- MiroFish `retry.py` → `utils/retry.py` (指数退避)
- MiroFish `__init__.py` → `app/__init__.py` (Flask 应用工厂)

### 当前状态

- 骨架代码完成, 所有 Tool 的 `execute()` 方法为 TODO stub
- 需要 `pip install -r requirements.txt` 安装依赖后才能运行
- 下一步: 接入四库驱动 (Neo4j/MySQL/Milvus/Redis) + 实现 Tool 逻辑

### 报告更新

- `architecture_report_v2.html` 落地架构 Tab 新增:
  - **第九节**: 后端工程骨架 (参考 MiroFish) — 项目结构 + ReAct 循环实现 + 与 MiroFish 差异对比
  - **技术栈表** (第八节): 新增 Flask/LLM网关/ReAct框架/Agent日志 4行
  - **论文依据表** (第十一节): 新增 MiroFish 参考
  - 章节重编号: 九→十(Schema), 十→十一(论文)
  - 日期更新: 2026-04-05

---

## 2026-04-05: 四库存储 Schema 设计

### 做了什么

设计全部四库的存储 Schema, 输出完整设计文档 `docs/arch_v2_db_schema.md`。

### 设计内容

| 数据库 | 设计 | 数量 |
|--------|------|------|
| **MySQL** | 11 张表 DDL (Hybrid 模式: 核心列+索引 + JSON 列) | 11 tables |
| **Neo4j** | 2 张图: Graph A (知识图谱 4 Label + 10 Rel) + Graph B (简历实体 19 Label + 24 Rel) | 2 graphs |
| **Milvus** | 4 个 Collection: skill/jd/resume/conversation vectors (BGE-M3, 1024dim) | 4 collections |
| **Redis** | 3 类 Key: session (HASH+LIST) + cache (STRING) + task (HASH) | 3 categories |

### 关键设计决策

- **Hybrid 模式**: 高频查询字段拆列建索引 + 完整 JSON 保留冻结 Schema 结构, 避免过度范式化
- **career_report 6 章拆列**: ch1~ch6_json 独立列, 支持 Phase 1 骨架即填 + Phase 2 流式插入
- **Neo4j 双图分离**: Graph A (L1 知识图谱, 确定性结构) vs Graph B (L0 实体抽取, 每份简历独立)
- **conversation_vectors**: student_id 字段支持跨会话 RAG 检索
- **Redis 滑动窗口**: session:history 用 LIST + LTRIM 存最近 20 条消息

### 状态

- **软冻结**: 允许加列/加索引, 不允许改已有列/删列
- 完整设计见 `docs/arch_v2_db_schema.md`

### 报告更新

- `architecture_report_v2.html` 第七节大幅扩充:
  - ASCII 架构图更新: 标注各库数量 (2 Graph / 11 Tables / 4 Coll. / 3 类)
  - 新增 MySQL 11 表明细表 (表名/层/核心索引/JSON列/说明)
  - 新增 Neo4j 双图对比表
  - 新增 Milvus 4 Collection 表
  - 新增 Redis Key Pattern 表
  - 数据流向更新: 详细到每层每库的写入/查询路径

---

## 2026-04-05: 设计拷打 (第二轮) + HTML 报告重构

### 设计拷打

发现 10 个待解决问题 (实现时逐个处理):

1. **Text-to-Cypher 数据隔离** — Graph B 需强制注入 student_id 过滤, 防止跨学生数据泄露
2. **match_result 覆盖策略** — 简历更新后重匹配, ON DUPLICATE UPDATE 还是版本化?
3. **Embedding 生成时机** — BGE-M3 同步生成 vs 异步生成, 需明确策略
4. **conversation_vectors 膨胀** — 需存储策略: 只存 user 消息? 定期清理?
5. **Agent 上下文冷启动** — 首次对话无画像时, Agent 应引导上传简历
6. **Pipeline 并发冲突** — 同 student_id 同 pipeline 的 running 状态需互斥
7. **知识库导出 (R-09)** — 需 `/api/graph/export` 端点, 具体格式待定
8. **career_report 并发写入** — ThreadPool×4 并行 UPDATE 同行不同列, 注意行锁
9. **Neo4j Graph A vs B 命名隔离** — Graph B 的 Skill 应改名 ResumeSkill 避免混淆
10. **Redis Session 过期恢复** — TTL 2h 后需从 MySQL conversation 恢复 session

### HTML 报告重构

- **左侧固定 TOC 导航栏**: 250px 宽, 11 + 5 = 16 个章节跳转链接
- **Tab 联动**: 切换落地架构/创新点 Tab 时, TOC 自动切换对应目录
- **Scroll Spy**: 滚动时自动高亮当前章节 (红色左边框)
- **Back-to-top**: 右下角浮动按钮, 滚动 400px 后出现
- **移动端适配**: ≤900px 侧边栏隐藏, 左上角汉堡按钮展开
- **Smooth Scroll**: `html{scroll-behavior:smooth}` + `scroll-margin-top:20px`
- **16 个锚点 ID**: prod tab 11 个 (`p-metrics` ~ `p-papers`) + inno tab 5 个 (`i-evolution` ~ `i-summary`)

---

## 2026-04-06: MVP 目录清理

### 问题

`mvp/` 目录混乱: `analysis_layer/` 是早期实验已被 `entity_extractor/` 取代; `entity_extractor/` 内混着重复文件、测试、缓存和生成输出。物理解析层 (`phase1_physical_layer.py`) 被 `phase1_enhanced.py` 混淆。

### 执行

**删除 `analysis_layer/` 整个目录**（早期实验废弃）:
- `1.py` (无意义命名的实验脚本)
- `physics_analysis.py` (phase1 的早期原型)
- `physics_result.json`, `resume_analysis.json` (生成输出)
- `requirements.txt` (过时依赖)

**移动**: `analysis_layer/resume_parse/` → `data/mineru_output/` (MinerU 预解析数据, 管线依赖)

**删除 `entity_extractor/` 中的废弃文件** (~15 个):
- `phase1_enhanced.py` — 最大混淆源, `nsle_pipeline.py` 引用的是 `phase1_physical_layer.py`
- `test_*.py` (5个), `check_gpu.py`, `run_demo.py`
- `entity_extraction_result_nsle.json`, `student_profiles.jsonl` (生成输出)
- `demo_distillation_data/`, `demo_sft_output/`, `self_learning_data/`, `learned_rules/` (空/废弃目录)
- `test_samples/`, `__pycache__/`

**删除**: `mvp/key.txt` — 明文 API Key 安全隐患

**路径修复**: 3 个文件中 `analysis_layer/resume_parse` → `data/mineru_output`
- `nsle_pipeline.py` (DEFAULT_MINERU_BASE + DEFAULT_OUT_PATH → outdata/)
- `block_classifier.py` (demo 函数)
- `mineru_caller.py` (demo 函数)

### 清理后结构

```
mvp/entity_extractor/       ← NSLE Pipeline (L0) 唯一模块
├── nsle_pipeline.py         ← 唯一入口 (Phase 1-4)
├── phase1_physical_layer.py ← Phase 1: 物理解析
├── phase2_symbolic_privacy_layer.py ← Phase 2: 符号化
├── phase3_reasoning_core.py ← Phase 3: LLM 推理
├── phase4_logic_guard.py    ← Phase 4: 逻辑守卫
├── output_adapter.py, block_classifier.py, mineru_caller.py
├── skill_matcher.py, schemas.py, evaluation.py
├── layout/, dictionaries/, prompts/, rules/
├── self_learning/, training_templates/
└── 文档: entity_extraction_schema_frozen.txt, input_layer_flowchart.md
```

---

## 2026-04-06: Input Router 统一输入路由器

### 背景

老师提出：先理清简历和JD数据的输入形态。梳理后发现：
- 简历: 用户上传 PDF/Word/图片, 或通过表单填写 (结构化)
- JD: 竞赛数据是 JSON (已结构化), 用户可能复制粘贴 TXT 或上传 Excel
- Phase 1 物理解析层只应处理**非结构化输入**, 结构化输入应跳过

### 新增文件

- `mvp/entity_extractor/input_router.py` — 统一输入路由器

### 架构设计

```
InputRouter (统一入口)
├── 非结构化通道 → Phase 1 物理解析 → document_blocks → Phase 2-4
│   ├── PDF  → MinerU 提文本 + YOLOv10 版面检测
│   ├── Word → python-docx 提文本 + 块分类 (复用 Phase 1 分类器)
│   ├── Image → YOLOv10 + OCR (TODO)
│   └── TXT  → 块分类
│
└── 结构化通道 → 字段映射 → 直接输出 Schema (跳过 Phase 1-4)
    ├── JSON (竞赛 jd_data.json) → 字段映射 + IT过滤(R-01) + HTML清洗
    ├── Excel → pandas + 字段映射
    └── 表单 (dict) → 直接透传
```

### 关键实现

- **InputRouter 类**: `route()` / `route_text()` / `route_form()` 三个入口
- **RouteResult**: 统一输出, `is_structured` 标记是否跳过 Phase 1
- **Word 通道**: `extract_docx_paragraphs()` + `docx_paragraphs_to_blocks()`, 复用 Phase 1 块分类器
- **JD 映射**: `map_jd_record()` 竞赛字段→Schema + `_is_it_position()` R-01 过滤 + `_clean_html()` 详情清洗 + `_parse_salary()` 薪资解析
- **图片通道**: 完整实现, 双策略 (YOLOv10 区域级 OCR / 全图 OCR 回退)
- **OCR 引擎**: 三级回退 PaddleOCR > Tesseract > EasyOCR, 统一抽象基类 `_OCRBase`
- **YOLOv10 统一骨干**: 单类检测模型 (best.onnx), 格式无关的版面区域检测
- **测试脚本**: `test_input_router.py` 一键测试 6 种通道 (PDF/Word/Image/JSON/TXT/Form)
