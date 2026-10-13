# 职导星图：平台级 AI Native 能力架构

Status: Working architecture（待逐条金路径验收；不是功能已接线声明）
Date: 2026-10-09

## 0. 纠偏：对象不是聊天 Agent，而是整个平台

平台同时有**会话式交互**（AI 助手、后续专家视角）和**功能内交互**（简历导入、画像、匹配、图谱、报告、成长分析、课程/项目/网站推荐等）。这两种表面不应各造一套 Agent，也不应把所有页面强行变成聊天框。共用的是身份、能力调用、证据、任务状态、成果物和模型预算；不同的是用户界面及该功能是否真正需要模型自主决策。

三类能力要分开：

1. **确定性领域工作流**：解析状态、四维评分、人岗匹配、存储、进度更新。模型可以解释或提出候选，不能替代规则分或伪造完成。
2. **LLM 辅助的领域任务**：结构化简历、报告文案、成长建议、推荐理由。固定输入/输出 schema、版本与证据；不必开一段独立聊天会话。
3. **用户可交互 Agent**：理解开放意图、澄清、选择/串联授权能力、比较证据、陪伴决策；图谱是其工具之一，专家身份是受限视角，不是每个页面一个自治 Agent。

## 1. 当前实际入口与断点

以下“可达”指从已注册 Flask 路由能追到代码；不等于真实数据库/模型已验收。前端生产默认 mock、开发默认 http，mock 页面不能证明后端能力。

| 功能域 | 当前可达入口/路径 | 平台级断点 |
| --- | --- | --- |
| AI 助手会话 | `/api/agent/chat` → Supervisor → ModeRuntime → LangGraph ToolNode；附件另经 `/api/agent/files` | `/chat/stream` 先执行完整回合再回放事件，非真实增量流。JSON/SSE 的 Supervisor 输入规范化已共用，但实时进度仍未实现；旧引擎不在此验收范围。 |
| 简历/附件 | `/api/profile/student/upload`→PipelineRunner；`/api/pipeline/ext/parse/upload/resume`→L0FullParser；Agent 文件→AgentFileService；Agent `pipeline_run` 又调用旧 L0ResumeParser | 三套解析/任务/存储语义。`agent_file` 已解析不等于 `student_profile` 已更新。 |
| 画像/匹配/报告 | REST、`PipelineRunner`、Agent `pipeline_run`/`portrait_generate`/`report_generate`、若干 `/pipeline/ext/*` 直调生成器 | 同一动作多套执行器。`profile` 上传返回的 task_id 与 PipelineRunner 写入的 task_id 不同；`report` 返回 ID 与 L4 实际生成的 ID 不同；工具可能把 `success:false` 写成 completed。 |
| 职业/学习推荐 | `/api/career` 确定性推荐、`/api/match` hybrid retrieve、`/api/learning/recommend` SQL 规则、Agent planner 的 graph/skill/learning 工具 | 至少四种推荐语义/分数/来源；课程历史画像可读取请求给的 student_id。尚无统一来源、时效和反馈契约。 |
| 图谱与语义证据 | 主 Agent 的 `graph_query`；页面 `/api/graph`；planner 的 `skill_recommend/learning_path`、`db_query`；qa/match 的 `semantic_search`；L1/扩展管线写图 | `graph_query`/公共 Graph API 已收束，但 planner 的另两工具仍可能直接读混存 `Skill`；`db_query` 固定 SQL 模板仍可读未隔离的岗位画像表；向量检索的 `all` 集合未按用户过滤 resume/conversation。**单点图谱修复不是整个证据面的隔离闭环**。L1 写入隔离仍未验收。 |
| 报告 AIGC/成长分析 | `/api/agent/career-suggestions`、`/api/ai/growth-analysis` 直接查业务表、装 prompt、调 LLM；旧 `/api/report/*` 与 `/api/match/*` 可读业务结果 | 绕开会话的身份/预算/证据/任务契约；请求体 userId/reportIds、路径 student_id/report_id 不能充当授权依据，需在资源层做 owner 约束。 |
| 知识库/管理 | `/api/admin` 普通上传、`/api/admin/kb` 智能抽取 | 智能抽取目前只取 `content[:3000]` 且同步处理，不能以“上传成功”代表全文索引完成。 |
| 页面内 AI | AI 助手 HTTP 会话；职业分析 Drawer 本地预设；报告页本地动画/mock；MyReports 吉祥物一次性 growth-analysis；RoleChat 独立 `/api/roles/chat`（现 501） | 多处假流式/假进度、独立或断裂会话；RoleChat 每消息换 session_id，角色选择未传到主 Agent；画像 HTTP 流固定 DEMO_STUDENT_ID。 |

跨链实测（无外部 DB 替身）进一步证明：`pipeline_run.params` 是模型可写 JSON 字符串，门禁只覆写顶层 student_id，业务却读取字符串内的另一个 student_id；`portrait_generate` 可用 `name` 当别人的 student_id，`report_generate` 固定写 `agent_user`。另 Agent 工具 L1 调用不存在的 `GraphStorage.save_skills_to_graph` 方法，异常被吞成 `success:false` 后外层仍可能标 completed。Agent 的 `pipeline_task` 候选成果物也未绑定实际画像/匹配/报告行，仅靠工具调用和摘要就可能被启发式提交；“计划已完成”不等于领域成果已存在。这些不是提示词能补救的问题，必须先修执行边界、再接平台 UI。

语义检索亦经无 DB 替身验证：`semantic_search(collection='all')` 对 `resume_vectors`、`conversation_vectors` 调用未带 owner filter，另一学生的模拟简历片段进入工具结果；真实业务库泄漏仍未验证。证据输出也不能只靠类型声明：`turn_contract.py` 有 `citation` 公开 part，但当前业务工具尚未形成贯穿“输入文件/版本 → 结构化行 → 图/向量 chunk → 规则/模型版本 → 页面引用”的稳定来源 ID。

## 2. 目标：一套 Capability Runtime，两个用户表面

```text
会话 / 页面按钮 / 后台触发
  → authenticated CapabilityCommand（actor、resource refs、intent、idempotency key）
  → Capability Runtime（政策/权限、预算、状态、事件、审计）
       ├─ Conversation Graph：澄清、工具选择、解释、跨功能 handoff
       ├─ Domain Workflow：简历→画像→匹配→报告→成长计划
       └─ Retrieval/Generation：公共图谱、岗位/课程/项目来源、报告 AIGC
  → authoritative Domain Store + Artifact Registry + Evidence refs
  → CapabilityRun/TurnOutcome（相同 run_id/status/next_action）
  → 聊天消息或功能页卡片、进度、审批、可打开成果物
```

**唯一能力契约**不是让所有 API 改成 `/chat`，而是让 REST 页面操作与 Agent 工具调用同一领域服务/工作流，并返回同一权威运行记录。最小字段：

这里的 Capability Runtime 是**项目自己的身份与领域结果薄契约**，不是再写一个编排器、队列、事件协议或文件解析框架；会话/任务/前端事件的通用机制优先使用下文列出的开源实现。

| 契约 | 必须区分的含义 |
| --- | --- |
| `actor_scope` | 仅从 Principal/服务端上下文获得；不能从 prompt、工具 JSON 字符串、请求体 userId 或页面常量获得。区分 public、student-owned、admin。 |
| `run_id` / `session_id` / `artifact_id` | run 是一次执行；session 是可选会话；artifact 是可再次打开的业务成果。禁止自造一个返回 ID、库里写另一个。 |
| `status` | `queued/running/needs_input/approval_required/partial/completed/failed/cancelled`；只有权威领域结果和持久化成果均成功才是 completed。 |
| `evidence_refs` | 文件页/块、画像/匹配版本、公共图谱路径、岗位/课程/网页来源与抓取时间；图谱/向量 retrieve 不改确定性总分。 |
| `artifact_refs` | 指向画像、匹配、报告、计划的权威 ID 和版本；聊天与页面只放安全 preview，不复制完整私有 JSON。 |
| `budget/usage` | 每步模型能力、上下文窗口、实际 provider usage、工具/时间上限、截断与部分完成标记。 |
| `events` | 执行时产生且可重放，不在整个任务结束后伪造“实时进度”；SSE/HTTP/history 对同一 run 折叠一致。 |

页面任务与会话之间用 **handoff** 连接：例如上传简历形成 `resume_artifact_id`，画像页触发同一 `profile` capability，AI 助手拿 artifact refs 解释“为什么”；用户在聊天里改变目标岗位后，再触发匹配 capability。上下文只传版本化引用和必要摘要，不把每页的临时 JSON 粘进会话 prompt。

## 3. 各功能域怎样 AI Native，而非“加聊天框”

| 功能 | AI Native 的用户动作 | 谁做权威工作 | 主 Agent 作用 |
| --- | --- | --- | --- |
| 简历 | 上传、逐页证据查看、缺页重试、确认结构化字段 | 文档解析器 + canonical profile workflow | 解释覆盖不足、只问缺失字段，不把首屏当全文。 |
| 画像 | 生成/更新、对维度证据提出异议、版本对比 | 确定性画像服务 | 解释维度与改善空间；不得凭话术调高分。 |
| 匹配/职业探索 | 选岗位、看硬约束和差距、比较方案 | 规则 scorer + 带来源检索 | 澄清目标、比较路线、解释可信度和假设。 |
| 图谱 | 展开岗位路径/技能先修、追溯来源 | 公共/个人分区图谱查询 | 按需检索并解释，不建第二个图谱会话；无隔离的 Skill 不可用要明说。 |
| 学习/项目/网站推荐 | 可筛选、收藏、反馈、继续规划 | 有来源和时效的候选检索 + 排序/规则 | 结合用户目标说明适配理由，不能凭模型记忆编链接。 |
| 报告/AIGC | 选择目标、预览草稿、批准生成、打开版本 | L4 任务 + 结构化输出验证 + 报告存储 | 撰写/解释建议、标明事实与推测；未持久化不称完成。 |
| 管理知识库 | 上传→解析→提取→人工核查→发布 | 异步摄取/索引工作流 | 帮管理员审核冲突与来源，不把前 3000 字当全文。 |
| 专家视角 | 在同一任务中选择职业教练/简历顾问等解释方式 | 同一会话与能力权限 | 用受限 Prompt/Skill 调整视角；不复制身份、工具或会话内核。 |

## 4. 开源复用选择（先复用，后做最薄适配）

| 层 | 选择 | 不选择/进入条件 |
| --- | --- | --- |
| 会话与多步编排 | **保留已运行的 LangGraph 1.2.9**，把确定性节点和需要 Agent 决策的节点分开；下一步复用其 checkpointer、interrupt、stream，而不是扩写 `session_runner.py`。官方：[持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、[中断](https://docs.langchain.com/oss/python/langgraph/interrupts)、[流式](https://docs.langchain.com/oss/python/langgraph/streaming)。 | `Deep Agents` 已提供文件/上下文/技能/审批 harness，但当前 `mkg` 是 Python 3.10、`langchain-core 1.4.9`，而其当前主线要求 Python >=3.11、`langchain-core>=1.6.7`，**不能原地安装替换**。以隔离 Python 3.11+ 环境做“长简历问答+工具+权限” spike，验证后再决定是否迁移。[官方能力](https://docs.langchain.com/oss/python/deepagents/overview)、[主线依赖](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/pyproject.toml)。 |
| 长任务 | **优先评估已安装 Celery 5.3.6 + 现有 Redis** 承担 OCR、画像、报告、KB ingestion 的可靠执行；保留 MySQL 业务 run/artifact 状态为权威。Celery 提供任务状态/重试，但取消与幂等仍需本项目契约。[官方任务指南](https://docs.celeryq.dev/en/stable/userguide/tasks.html)。 | 不继续扩大进程内 `fork_jobs`；Temporal 功能更强但需新增服务和部署成本，只有 Celery 不能满足多日审批/补偿时再比选。[Temporal Python](https://docs.temporal.io/develop/python)。 |
| 前端 Agent 交互 | 对 Vue 3 **优先做 CopilotKit Vue v2 + AG-UI 的一条隔离金路径 POC**，验证会话、页面内工具卡、审批、进度与恢复；协议/SDK已有这些语义。[Vue SDK](https://docs.copilotkit.ai/reference/vue/index)、[AG-UI 事件](https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/sdk/js/core/events.mdx)。 | 当前 Flask/Python 3.10 不能直接假设 CopilotKit 原生 Python runtime 可用：官方说明 TypeScript runtime 可独立运行，Python runtime 需 Intelligence 且 Python 3.11+；可能需要小型 TS runtime/AG-UI adapter。先 POC 测兼容、身份透传、许可证与部署，再决定迁移，不盲目全前端替换。[运行时约束](https://docs.copilotkit.ai/langgraph-python/copilot-runtime)。 |
| 多模态文件 | 沿用已验证 MinerU/Mammoth/Document Access 分页与原始文件/派生证据；图片、语音另接已验证解析器。 | Deep Agents 的虚拟文件工具可作为长上下文候选，但它不是简历结构化结果的权威存储，也不自动覆盖 DOCX 文本框。[官方文件能力](https://docs.langchain.com/oss/python/deepagents/overview)。 |
| 外部工具互联 | 将来接 WorkBuddy/Qoder/网站检索等外部能力可用官方 MCP SDK/协议做适配，业务身份和工具许可仍由本平台授权。 | 内部每个 Python 函数先套 MCP 会增加无益复杂度；MCP 不替代 owner 检查。[MCP 官方说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)。 |

OpenCode/Codex 本地快照继续用于文件分页、预算、停止/恢复等可测契约，参考 commit/模块见 `docs/architecture/agent-upstream-adaptation.md`；不能把它们编码 Agent 的 shell/文件权限原样给职业规划平台。模型、prompt、前端框架或协议的更换必须先过一条真实金路径的端到端结果，而不是依靠 README 相似度。

## 5. 迁移顺序和验收门槛

1. **P0：停止错误的完成/身份/ID。** 修 `pipeline_run` JSON 内 student_id、portrait/report 工具 owner；`success:false` 不得 completed；修 L1 不存在的方法与 L0 无 artifact 交接；profile/report 返回 ID 必须能从权威库查到；growth-analysis/career-suggestions/learning/match/report 等用户读面绑定 Principal。统一审计 `db_query`、`skill_recommend/learning_path` 和语义检索的用户作用域，不能因 `graph_query` 410 就放过旁路。对“用户 A 输入用户 B ID”和“任务失败却显示完成”各建负向测试。
2. **P1：一条黄金业务链。** 以真实简历贯穿“解析→结构化确认→画像→匹配→报告”，REST 按钮与主 Agent 触发同一 capability；每一步核对 run_id、业务 ID、状态、证据页、持久化行与 UI；现有多套解析器只保留兼容入口/薄适配，不再并行演化。
3. **P1：真实运行事件。** 先让后端执行中发出可重放的权威状态，再选 AG-UI/CopilotKit Vue POC 或保留当前 UI 的协议适配；前端超时不能本地合成服务端失败，也不能拿定时器假装任务进度。
4. **P2：功能页逐项接入。** 职业分析、画像、图谱、课程、报告分别通过 capability 卡片/上下文 handoff 与同一 AI 助手互通；优先解决角色 session ID、固定 DEMO_STUDENT_ID、空报告回填 mock 和页面假流式。没有后端证据的功能明确标 demo。
5. **P2：质量评估。** 在隐私隔离的黄金集上评测全文消费、职业建议事实性、技能/项目推荐链接时效、澄清问题数量、证据支持率、跨页面连续性；按模型/提示词/工具版本追溯。OCR 识别正确不等于这些指标已通过。

验收原则：从**用户动作 → Principal → run → 实际工具/领域执行 → 权威业务行/文件 → UI 展示**逐段证明；只看到模型回复、provider 成功、HTTP 200 或动效完成均不算业务完成。

## 6. 证据入口与当前验证边界

- 后端会话、权限及工具适配：`backend/app/api/agent.py`、`backend/app/services/agent_runtime/{supervisor,mode_runtime,scoped_invoke,plan}.py`、`backend/app/services/tools/pipeline_run.py`。
- 同功能多路径：`backend/app/api/{profile,report,pipeline}.py`、`backend/app/services/pipeline.py`、`backend/app/api/pipeline_ext_*.py`；对照真实写入行与返回 ID，而非只看响应。
- 前端真实/模拟分流与页面任务：`cpa-xingtu/src/api/config.ts`、`cpa-xingtu/src/stores/agentChat.ts`、`cpa-xingtu/src/components/RoleChat.vue`、`cpa-xingtu/src/composables/useAgentPortrait.ts`、`cpa-xingtu/src/views/student/{CareerAnalysis,MyReports}.vue`。
- 目前跨链门禁结论由无 DB 的替身调用证明参数能越过门禁、失败能误记完成；真实用户数据泄漏、全部业务行写入及全平台用户旅程仍需按 P0/P1 在服务、数据库与 UI 上验收。不能把源代码可达性或替身结果描述成已完成生产端到端攻击/修复。

## 7. 本轮落地状态（2026-10-09；不覆盖上面的审计快照）

| 切片 | 已落地 | 仍未成立的验收门槛 |
| --- | --- | --- |
| P0 身份/结果 | 主 Agent、match、职业评估、学习进度、部分旧 `/pipeline/ext` 与图构建入口已绑定服务端 Principal/权威记录；失败、草稿和未验证成果不再冒充完成。详见 `../guides/p0-identity-and-result-truth.md`。 | 其余历史资源 API 仍需全面审计；不能宣称全站完成。 |
| 公共图谱 | 独立 `PublicSkill` 标签、受控种子和 fail-closed 查询已接线；本机真实 Neo4j 已发布 11 节点/4 先修边并通过最小实测。详见 `../runbooks/public-skill-partition.md`。 | 这不是完整技能/岗位图恢复；个人图 owner 查询、岗位关联和历史混存清理未完成。 |
| 长任务/事件 | Agent 媒体文件采用宿主机 Celery worker + Docker Redis 投递、权威文件状态与可重放事件；真实 MinerU Sidecar 在宿主机运行并能解析本地双页样本。Pipeline task 已有 MySQL 事务事件 journal 与 owner/cursor API，页面使用真实事件。详见 `../guides/durable-media-jobs.md`、`../guides/pipeline-event-journal.md`。 | worker kill/AOF 故障注入未做。Pipeline journal 不会自动恢复执行；L0/画像/报告未接持久队列。 |
| TurnOutcome/交互 | HTTP、SSE 终态事件、会话历史投影共用 `completed/needs_input/running/partial/failed`；页面→会话用服务端验证的 task 引用交接，客户端摘要不进入权威上下文。 | `completed` 仅表示本轮回复，不表示业务成果；生产 Agent 的 LangGraph checkpoint/interrupt 未接，`resume_state=not_available`。AG-UI 仍是待评估 POC。 |
| 金路径/质量 | L0 简历候选、逐页块证据、用户确认和有证据才落库的 L2 共用 `PipelineRunner`；L3/L4 有受控岗位发布、匹配/报告持久快照适配。LangGraph/LLMClient 有同步调用前预算及 usage 切片。详见 `../guides/resume-confirmation-golden-slice.md`、`../guides/match-report-source-gate.md`。 | 当前真实岗位目录为空，L3/L4 对实际用户仍停 `partial`；评分/建议质量、DOCX 嵌图、逐字 CER、全链 token 与跨页面完整旅程未验收。 |

上述实现选择继续遵守“优先复用成熟开源能力、仅写最薄业务适配”的硬规则；媒体队列复用已安装 Celery，主 Agent 继续使用 LangGraph。不得用新增状态字段或单元替身测试替代真实业务金路径验收。

**前端真实模式边界**：职途导航的上传/候选确认/L2 卡片只展示权威任务 ID 和后端结果；旧七维星图动画与定时进度仍只属于 mock 适配。粘贴文本尚未接入可确认的持久任务，真实模式明确拒绝这条捷径，不把本地技能抽取与可选保存合成“完成”。
