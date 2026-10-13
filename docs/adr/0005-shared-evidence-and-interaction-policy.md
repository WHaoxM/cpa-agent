# ADR 0005 — 共享证据层，按交互权限区分 Ask / Agent / Experts

Status: Accepted boundary; implementation and live acceptance in progress
Date: 2026-10-10

## Context

平台已有会话和功能页两类入口。已确认简历、画像和其它业务成果存在于权威业务表，`resource_catalog.py` 能按服务端学生身份发现资源、按 URI/section 分页读取。然而 Ask 当前只收到限长的 `overview_only`，在真实请求中知道用户有 35 项技能却无法列出前 10 项，建议用户重新上传，并把该问答回合记为 `completed`。Agent 可以调用 `career_data_read` 或 `graph_query`，但把这两个通用 Agent 工具开放给 Ask 会混淆只读问答与自主执行，并复制权限、预算和状态语义。

局部修补 `graph_query` 的 schema 只能修 Agent 工具调用，不能补足 Ask 的资料获取。历史设计将 Ask / Agent / Experts 的数据通道随模式分裂；本决策将**证据访问**与**执行权限**分离，而不另建用户资料副本或第四套编排器。

## Decision

1. 保留一个 Principal 约束的会话与权威业务存储。页面、Ask、Agent、Experts 共享 `resource_catalog` 的版本化 URI、领域读取器、证据页/块、覆盖与分页合同；资源内容不复制到黑板或 Agent 专用用户库。权限必须在服务端读取点再次验证，不能以用户消息中的姓名、ID 或 URI 冒充 owner。
2. 在模型调用前建立有界、只读的 **EvidencePack**：由已验证的页面引用和/或受限检索计划选择资源及 section；服务端校验计划、读取业务原文、记录来源/版本/coverage/remaining。模型可辅助选择候选资源，但其输出不是可执行的数据库语句、图查询或业务工具调用。检索失败、预算截断和仅有概览均须显式表示，不得静默当作全文。
3. **Ask** 采用检索→回答的受控路径。Ask 可以使用相关的已授权业务数据和附件回答，但不进入 Agent 的通用工具循环，不执行业务写入，不因模型一句话创建画像/匹配/报告。其产品承诺是“基于哪些资料回答到什么范围”，不是“零数据库读取”。
4. **Agent** 在同一 EvidencePack 上判断开放目标，按现有授权、预算、审批与 LangGraph 工具/领域工作流执行。图谱仍是同一主 Agent 的受控能力（ADR 0004）；确定性评分与权威成果仍由领域服务负责（ADR 0002）。
5. **Experts** 复用同一身份、证据和领域执行器，专家仅改变受限视角、计划和输出要求，不建立第二套业务数据或独立长期记忆。既有冻结子 Agent 拓扑暂不扩大；合并运行内核必须逐步迁移，不能凭本 ADR 宣称已完成。
6. 区分**回答回合完成**、**证据覆盖完整**与**业务成果完成**。`TurnOutcome.completed` 只说明本轮回答结束；资料缺页/未展开、来源不可用或业务候选未确认应另有公开 coverage、continuation 和权威业务状态。模型不得把本轮回答完成写成画像、匹配或报告完成。

## Reuse and adaptation boundary

- 已运行的 LangGraph 继续承担动态 Agent 工具循环及后续 checkpoint/interrupt；Ask 的固定检索→回答不另写循环。LangChain 官方把固定工作流与动态 Agent 分开，并把先检索再生成的 2-Step RAG 与模型动态取证的 Agentic RAG 分开：<https://docs.langchain.com/oss/python/langgraph/workflows-agents>、<https://docs.langchain.com/oss/python/deepagents/retrieval>。
- `opencode/` commit `388406238b` 的 `packages/opencode/src/session/tools.ts` 和 `_agent_reference/codex/` commit `a06545b` 的 `codex-rs/core/src/tools/handlers/mcp_resource_spec.rs` 说明“发现资源 URI → 读取精确资源”的通用合同。本项目复用其合同思想和现有 `resource_catalog.py`，不复制其 shell/file 工具权限，不声称已实现 MCP server。
- LangChain 的 context engineering 将每次模型输入与工具可执行权限分开；本项目在既有 `DefaultContextAssembler` 前准备结构化证据，不把权限判断放进提示词：<https://docs.langchain.com/oss/python/langchain/context-engineering>。

## Migration and acceptance

1. 先建立共享 EvidencePack 与聚焦测试：owner 正反向、资源版本、section/offset、10/35 分页、预算截断、来源暂不可用；不改写业务行。
2. Ask 接入同一检索层，替换“只注入 overview 且不能展开”的行为；不绑定 `graph_query`、`career_data_read` 等通用 Agent 工具。无关闲聊不访问私有资源，显式引用或有依据的选择才读取相应 section。
3. Agent/Experts 渐进复用相同证据合同，保留现有执行权限和业务权威门禁；去除重复资源摘要与模式专属数据副本。不要先大改所有页面或模型提示词。
4. 对同一真实用户、同一问题“列出已确认技能前 10 项、来源和下一页”，Ask 应返回本人 10 项及可验证来源、说明下一页，不要求重传；Agent 应能读同一版本，若用户要求制定计划才进入授权执行；另一 Principal 不可读。真实 HTTP、模型输入证据、审计、业务表与前端展示逐段核对。
5. 只有 Ask/Agent/Experts 和至少一条页面引用在真实服务上通过上述门槛，才把“跨功能同证据层”称为接通。单测、HTTP 200、LLM 文本或此 ADR 均不等于完成。

## Consequences

Ask 多一次有界检索可能增加少量延迟，但避免工具循环的不可预测重试与全部用户数据进 prompt。检索计划和 EvidencePack 必须有独立预算、覆盖与审计；过度预取会增加隐私面和 token 消耗，因此默认最小必要 section。保留现有模式 API 以兼容前端，逐步将其语义收束为交互与执行政策，而不是三套互不相通的资料通道。
