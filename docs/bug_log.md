# Bug 日志

## 2026-04-21: Mermaid 系统架构图解析失败

### Bug #19: Redis 节点文本中的 `{id}` 被 Mermaid 误判为菱形语法起始符

- **文件**: `docs/system_architecture.md`
- **根因**: `flowchart` 节点文本中直接写 `session:{id}` / `task:{id}` 这类带花括号的占位符，Mermaid 解析器会把 `{` 当作 `diamond` 节点语法的一部分，导致 `Parse error`。
- **修复**: 将节点展示文本改为不含花括号的安全形式，如 `session-id`、`cache-portrait-job-id`、`task-id`。
- **影响**: 系统架构总图无法渲染，影响文档展示与答辩使用。

## 2026-04-06: Input Router 首轮测试 — 3 个 Bug

### Bug #1: Image 通道 `No module named 'layout.yolo_layout_detector'`

- **文件**: `mvp/entity_extractor/phase1_physical_layer.py` L694
- **根因**: `yolo_detector` 属性中 `from layout.yolo_layout_detector import` 缺少 try/except 双路径导入，standalone 运行时找不到模块
- **修复**: 改为 `try: from .layout... except: from layout...` 模式
- **影响**: Image 通道完全不可用

### Bug #2: JD JSON `argument of type 'NoneType' is not iterable`

- **文件**: `mvp/entity_extractor/input_router.py` `_is_it_position()` L285-286
- **根因**: `record.get("所属行业", "")` — 当 JSON 中字段值为显式 `null` 时返回 `None`（key 存在但 value=None），后续 `kw in None` 报 TypeError
- **修复**: 改为 `record.get("所属行业") or ""`，用 `or` 防御 None
- **影响**: 所有含 null 字段的 JD 记录解析失败

### Bug #3: PDF 通道返回 0 blocks (MinerU 路径不匹配)

- **文件**: `mvp/entity_extractor/input_router.py` `_route_pdf()` L870-874
- **根因**: 代码硬编码路径 `{stem}/auto/content_list_v2.json`，但实际 MinerU 输出为 `简历/auto/简历_content_list_v2.json`（文件夹名 ≠ PDF stem，文件名带前缀）
- **修复**: 新增 `_find_mineru_outputs()` 函数，两步搜索策略：精确匹配 → rglob 通配搜索
- **影响**: PDF 简历虽 PASS 但产出 0 个 block，等于空跑

## 2026-04-08: Pure LLM Benchmark — Qwen3.5 本地加载失败

### Bug #4: `qwen3_5` 架构当前环境不识别

- **文件**: `mvp/entity_extractor/test_llm_pure_benchmark.py`

## 2026-04-19: 后端系统能力缺陷修复 (6项)

### Bug #5: Agent ReAct 循环用正则解析 tool_calls，不可靠

- **文件**: `backend/app/services/react_agent.py` L200-280
- **根因**: 手写 `_parse_tool_calls()` 用正则从 LLM 文本输出中提取工具调用，LLM 输出格式不稳定时解析失败率高
- **修复**: 引入 LangGraph StateGraph + ToolNode + tools_condition，使用 LLM 原生 function_calling 替代正则解析。新文件 `langgraph_agent.py`，旧 ReAct 保留为 fallback
- **影响**: Agent 工具调用失败率高，对话质量差

### Bug #6: Pipeline L0/L2/L3 为空壳 stub

- **文件**: `backend/app/services/pipeline.py` L70-233
- **根因**: L0ParseResumePipeline.run()、L2GeneratePortraitPipeline.run()、L3RunMatchPipeline.run() 只返回固定字符串，不调用实际算法
- **修复**: L0调用L0ResumeParser+写入MySQL; L2调用PortraitGenerator+从Neo4j获取技能+写入MySQL; L3调用calculate_match_score+从Neo4j/MySQL获取技能+写入match_result/skill_gap
- **影响**: 所有管线端到端流程断裂，前端调API拿不到真实数据

### Bug #7: D1 无硬约束判定 (违反 R-08)

- **文件**: `backend/app/pipeline/entity_extractor/matcher.py` `_calc_d1_score()`
- **根因**: D1只算分数不判定pass/fail，不满足"技能不满足则不匹配"的竞赛规则(R-08)
- **修复**: 新增 `_calc_d1_with_constraint()` — 学历≥要求 AND (专业匹配 OR major_strict=false)，不通过则 match_result=false
- **影响**: 匹配结果可能将不合格学生判定为匹配成功

### Bug #8: D2 无语义匹配 (BGE-M3 未集成)

- **文件**: `backend/app/pipeline/entity_extractor/matcher.py` L157
- **根因**: D2 skill_coverage 只计算精确匹配+别名，未使用 BGE-M3 向量语义匹配(cosine>0.85)
- **修复**: skill_coverage 公式改为 (exact_count + semantic_count×0.8) / required_count，新增 semantic_matches 详情字段
- **影响**: 技能覆盖率偏低，"了解Python"无法匹配"掌握Python"等语义相似技能

### Bug #9: D3/D4 评分过于简化

- **文件**: `backend/app/pipeline/entity_extractor/matcher.py` `_calc_d3_score()`, `_calc_d4_score()`
- **根因**: D3只看经验数量+关键词; D4只看技能分类，未按冻结Schema的子维度评分
- **修复**: 新增 `_calc_d3_detailed()` (communication/teamwork/stress/responsibility + 规则加分) 和 `_calc_d4_detailed()` (competition/research/academic/leadership 各0-25分)
- **影响**: 画像评分不精确，无法提供具体维度弱项分析

### Bug #10: Agent 无 SSE 流式输出

- **文件**: `backend/app/api/agent.py`
- **根因**: /api/agent/chat 只支持同步JSON响应，前端AI助手无法实现逐字输出
- **修复**: 新增 `/api/agent/chat/stream` SSE端点，LangGraph stream_mode="messages" + Flask Response(text/event-stream)
- **影响**: 前端AI助手体验差，需等待完整回复才显示

## 2026-04-21: 合规性测试环境导入链缺陷

### Bug #11: 非LLM测试被 `openai` 依赖阻塞，pytest 无法收集

- **文件**: `backend/app/utils/__init__.py`
- **根因**: 顶层 `from .llm_client import LLMClient`，导致任何 `import app...` 都可能触发 `llm_client.py`，而 `llm_client.py` 顶层依赖 `openai`
- **修复**: 将 `LLMClient` 改为惰性导出：删除顶层导入，改用 `__getattr__()` 按需导入
- **影响**: 与 LLM 无关的 `matcher.py` / `portrait_generator.py` / `report_generator.py` 等纯逻辑测试在未安装 `openai` 的环境下全部无法执行，阻碍算法验证

### Bug #12: D1 学历别名未归一化导致硬约束误判

- **文件**: `backend/app/pipeline/entity_extractor/matcher.py`
- **根因**: `degree_rank` 只识别 `专科/本科/硕士/博士` 四个精确值，`大学本科` / `学士` / `研究生` / `博士研究生` / `大专` 等真实简历表述会落到 `rank=0`
- **修复**: 新增 `DEGREE_KEYWORDS` + `_normalize_degree()`，在 `_calc_d1_with_constraint()` 和 `_calc_d1_score()` 中统一先归一化再判定
- **影响**: R-04 / R-08 场景下，大量真实简历会被错误判定为学历不满足

### Bug #13: D1 证书匹配仅依赖原始字符串包含，真实别名无法命中

- **文件**: `backend/app/pipeline/entity_extractor/matcher.py`
- **根因**: 原逻辑使用 `rc in sc or sc in rc`，无法稳定处理 `CET-6 ↔ 英语六级`、`全国计算机等级考试二级 ↔ 计算机二级`、`AWS Certified Solutions Architect ↔ AWS认证` 等别名场景
- **修复**: 新增 `CERTIFICATE_ALIASES`、`_normalize_certificate_name()`、`_certificates_match()`，将证书匹配由裸字符串包含改为归一化后比较
- **影响**: `cert_match_rate` 偏低，进而影响 D1 分数与画像准确性

### Bug #14: NSLE 简历抽取链对真实样本存在执行级故障 ✅ 已修复

- **文件**: `backend/app/pipeline/entity_extractor/nsle_pipeline.py`
- **根因**: (1) Phase 1 标题分类无长度守卫，"研究" 关键词命中正文行导致教育 block 被误分为项目经验; (2) fallback 路径创建 dict 而非 DocumentBlock; (3) JSON 解析脆弱
- **修复**: Phase 1 `len(line) <= 20` 守卫 + fallback 统一创建 DocumentBlock + 健壮 JSON 抽取
- **影响**: 执行失败率 → 0%，教育抽取率 → 100%

### Bug #15: NSLE 当前缺少有效 certificate 抽取能力 ✅ 已修复

- **文件**: `backend/app/pipeline/entity_extractor/nsle_pipeline.py`
- **根因**: Phase 3 无 `_extract_certificates_with_llm`
- **修复**: 新增专用证书 LLM 抽取方法 + Phase 4 集成去重 + 知识库归一化接入
- **影响**: 证书字段就绪率 10/10，有 MySQL 后归一化由知识库驱动

### Bug #17: Phase 4 硬编码归一化字典不可扩展 ✅ 已修复

- **文件**: `backend/app/pipeline/entity_extractor/nsle_pipeline.py` Phase 4
- **根因**: degree/cert 归一化用 Python dict 写死在代码中，遇到未覆盖的实体变体就漏匹配
- **修复**: 移除硬编码 dict，Phase 4 调用 `EntityNormalizer` 查 MySQL `canonical_entity` 表 (别名+语义+OOV闭环)，无 MySQL 时优雅降级
- **影响**: 归一化规则可通过 Agent/Admin/数据库维护扩展，不需改代码

### Bug #18: Matcher/Profile 仍残留硬编码归一化规则 ✅ 已修复

- **文件**: `backend/app/pipeline/entity_extractor/matcher.py`、`backend/app/pipeline/entity_extractor/profile_normalizer.py`
- **根因**: `DEGREE_KEYWORDS`、`CERTIFICATE_ALIASES`、`MAJOR_ALIASES`、`AWARD_LEVEL_ALIASES` 写死在后端代码，冷启动/新增别名需改代码发布
- **修复**: 迁移到 `canonical_entity` + `normalization_rule`；新增 `EntityNormalizer.apply_external_rule()`，matcher/profile 全部改为数据库规则驱动
- **影响**: 可用 mock/seed 快速回放冷启动流程，后续规则由 Agent/Admin/DB 维护，不依赖代码硬编码

### Bug #16: Embedding 对等价短缩写/命名变体召回不足

- **文件**: `backend/app/services/embedding_service.py`（表现层面） / `backend/app/services/entity_normalizer.py`（阈值消费层）
- **根因**: 在 `0.85` alias 阈值下，`Python↔Python3`、`Vue↔Vue.js`、`K8S↔Kubernetes`、`JS↔JavaScript`、`TS↔TypeScript`、`Spring Boot↔SpringBoot` 等等价对大量未达阈值
- **修复**: 暂未修复，本轮先通过 embedding 真实评测确认主要矛盾是 false negative，而非 false positive
- **影响**: R-03 当前危险误命中风险可控，但等价技能召回不足，会导致真实技能覆盖率偏低
