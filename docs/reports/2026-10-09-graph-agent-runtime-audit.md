# 主 Agent 图谱能力运行链审查（2026-10-09）

## 当前结论

本项目**没有独立“图谱 Agent”**；`subagent_graphs.py` 的 `graph:` 是 LangGraph 引擎标签，不是知识图谱专家角色。按 ADR-0002 的拓扑冻结和产品选择，应由**同一主 Agent**理解用户问题、在 qa/planner 的白名单内调用受控图谱工具、拿到可验证证据后作答，不增加聊天入口或专家数量。

本轮经运行链审查发现共享 Neo4j 里 `:Skill` 既有种子，也由 `GraphStorage.save_skills` 从用户简历/JD 直接 `MERGE`，**没有可靠公共来源标记**；`JobPortrait` 可由运行时 JD 生成却没有 owner。因而“只读”或只限 `:Skill`/`:JobPortrait` 标签都不足以证明用户数据不外流。安全收口后：

- `GraphQueryTool` **只执行固定参数化 `CareerNode` 岗位列表与职业路径模板**，只投影 `role/level/start/target/steps`。LLM 只能返回 JSON 模板枚举和岗位实体槽位，不能提供可执行 Cypher。技能类问题明确告知“当前共享 Skill 图未分区，暂不可用”，不是回答“没有岗位/技能”。
- Flask `/api/graph/query` 只保留 `career_path` 的公共 `CareerNode` 标量结果；Skill/JobPortrait 模板、`/overview`、`/skill-neighbors`、`/skill-categories`、旧 `/career-path/<job_id>` 和 `/job-requirements/<job_id>` 返回 **410**。这是有意的**破坏性安全收口**，前端旧图谱视图需要显示不可用/降级，不能静默空图。
- `POST /api/graph/build` 在 strict 模式无 Principal 返回 401；有 Principal 时忽略 body 的伪造 `student_id`，绑定服务端身份。该修复只证明入口 owner 绑定，**不证明 L1 对共享图的写入已经租户隔离**。

## 实际入口和数据流

1. Agent API → `SupervisorService` → `ModeRuntime.chat`。Agent 模式 `_run_agent` 的 `keyword_route` / LLM router 把图谱意图落到 `qa` 或职业学习规划的 `planner`；Ask 不开放 `graph_query`。Experts 仅能按计划白名单使用。
2. `tool_policy.py` → `permissions.py` / `pre_tool_use.py` → `tool_factory.py` 的 BaseTool→LangChain 适配 → `gated_tools.py` 的模型/UI 双通道。`GraphQueryTool` 使用 Prompt Registry 的 `tool.graph_query.system` 从自然语言选模板、提取岗位名，服务端验证枚举/长度，调用 `Neo4jClient.execute_query(fixed_cypher, parameters, read_only=True)`。
3. Agent model view 含有限公共标量与总行数；UI view 仅条数摘要。查询 `LIMIT 50`，文本最多展示 10 条并显式说明其余未展示。没有证据 ID、cursor 或可点击关系图引用，因此暂不承诺“完整图谱解释”。
4. `/api/graph` 是**另一条 Flask API**，并未自动变成主 Agent 的证据。其目前唯一可用公共查询是 `career_path`；`rows` 返回 `start/end/steps`，不返回整节点/私有属性。

## 关键证据

- `backend/app/pipeline/entity_extractor/graph_storage.py`：`_batch_save_skill_nodes` 与 `_batch_save_entity_skill_relations` 将用户技能写进共享 `:Skill`；`save_job_portrait` 没有 owner。`backend/scripts/neo4j/init_neo4j.cql` 的公共种子 `Skill` 也没有 `is_public`/来源证明字段。无法在查询时安全区分“公开技能”和“用户独有技能”。
- 原 `GraphQueryTool` 允许 LLM 生成任意只读 Cypher，标量 `RETURN c.name`/`count(*)` 被丢弃；Neo4j Python driver 5.16.0 的 `Node.labels`、`Relationship.type` 是属性，旧展示逻辑把它们当方法。现在工具不执行任意 Cypher，也不展示整个 driver Node/Path；仅公共 `CareerNode` 标量投影。Flask 保留的职业路径 API 也只输出标量。
- 无法证明 Graph B/共享技能图真实数据库内容或模型最终职业回答效果：本轮真实 Neo4j/真实 LLM 主 Agent 自动工具选择尚未联调，不能把替身测试说成用户体验验收。

## 窄链验证

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_prompt_registry_golden.py backend/tests/test_graph_query_tool.py backend/tests/test_graph_api_public.py -q
```

结果 **19 passed**。覆盖 query-only 自然语言职业问题、BaseTool→LangChain adapter→PreToolUse gate→model/UI 输出、固定模板参数化、缺岗位澄清、模型输出 Cypher/Person/额外字段不触库、技能问答 fail-closed；另有 Flask 410/401/Principal 绑定、公共路径标量、混合 `CareerNode+Person` 节点过滤，以及输出 allowlist **仅 CareerNode / PROMOTION / TRANSFER / 必要字段**的负向断言。Neo4j 与 LLM 由可检查替身提供；无实际数据库读链/模型语义评分。

## 开源复用与边界

- 本地 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT）的 `packages/opencode/src/tool/tool.ts` 参数 schema/结果契约与 `src/tool/truncate.ts` 的显式截断/继续读取，是后续图谱证据分页的参考；未复制 TypeScript 内核。当前执行继续使用现有 LangChain `StructuredTool` + LangGraph `ToolNode`。
- Neo4j Python driver 5.16.0 为当前运行时；不再以解析整个 `Node`/`Relationship` 对象作为 Agent 证据。`LIMIT 50` 和固定标量列是应用层限流，未来还需数据库角色、超时和专用公共图谱数据源。
- 不引入全套 GraphRAG 索引或新增图谱 subagent。先建立 Graph A/B 公私分区、证据引用、工具预算与真实用户交互验证，再评估更大架构替换。

## 剩余风险与下一步顺序

1. **高：数据隔离**。`CareerNode` 目前从固定 seed/静态构建代码生成，路径可保留；但整个 Neo4j 仍混放用户图。应把公共知识图与个人简历图做独立数据库/凭证或可信 `graph_scope + owner_id` 迁移；给 `Skill`、`JobPortrait` 的每个现存节点补 provenance，不能简单恢复 410 模板。用两名隔离测试用户验证无 Principal、用户 A 查用户 B 以及跨用户写均失败。
2. **高：构建写入**。`/api/graph/build` 已绑定 Principal，但 L1 的 `type=full` 和 `jd_texts/profile_text` 仍可能写共享图；需跟踪底层任务、输入限额与 owner 标记，验证最终图状态，而非仅 HTTP 200。
3. **中：证据与分页**。公共路径输出没有稳定节点/边 ID、来源版本、查询 ID 或可展开关系图；model view 的通用 4k 截断也无图谱 cursor。应定义结构化 evidence contract 和续读路径，确保主 Agent 的推荐理由可追溯，且检索证据不改写确定性匹配分数。
4. **中：产品降级**。前端若调用已 410 的 overview/技能/岗位画像接口，必须显示“图谱能力待数据分区”并引导可用 CareerNode 路径或其他已验证检索，不能呈现空白图。真实 DB + 真模型 golden set 通过前，不应对用户承诺全量图谱 Agent。
