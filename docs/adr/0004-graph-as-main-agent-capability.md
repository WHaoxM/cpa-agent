# ADR 0004 — 图谱是主 Agent 的受控领域能力

Status: Accepted
Date: 2026-10-09

## Context

产品需要 AI Native 化，图谱参与岗位、技能、路径与证据解释。用户明确选择“同一主 Agent 的图谱能力”，而非独立图谱专家会话。既有 ADR 0002 已冻结 subagent 拓扑，并规定图谱检索只提供候选与证据，不改写确定性的匹配分。

当前运行面经 `ModeRuntime → scoped_invoke → gated graph_query` 触达 Neo4j；`subagent_graphs.py` 的 `graph:` 引擎标签是 LangGraph 执行标签，不是知识图谱 Agent。仓库还存在 `/api/graph` 页面查询接口，它也不代表第二个 Agent。`GraphStorage` 可写入 `Person` 简历实体，而旧图谱工具/公开邻居模板未明确区分公共图谱与用户图谱；只读并不能解决跨用户可见性。

## Decision

1. 保留**一个面向用户的主 Agent 会话**。图谱以受控 Tool/证据模块接入同一回合的身份、权限、预算、状态和用户出口；不新增 graph subagent，也不建立独立的图谱聊天记忆。
2. Agent 可读的公共图谱查询仅使用服务端固定、参数化模板，返回带来源/覆盖说明且已证明公共归属的岗位/路径证据。模型可以选择模板与参数，但不得提交任意 Cypher 作为可执行代码。当前 `:Skill` 节点会被用户简历/JD 写入并与种子节点混存，缺乏公共 provenance；在补齐分区或可信公共标记前，不开放该标签的公共查询或统计。
3. 含 `Person` 等用户实体的图谱数据不得进入公共工具或公共 `/api/graph` 查询面。未来若确需读取个人图谱，须先定义与 `Principal.student_id` 绑定的独立查询接口和负向跨用户测试；不能靠 prompt、结果脱敏或 read-only 标记代替授权。
4. 图谱证据只影响解释、候选与下一步建议，不修改 `match_result.total_score`。

## Trade-off and consequences

固定模板限制即席 Cypher 的表达范围，但换取可审计参数、稳定结果类型与明确数据作用域；新增业务图谱问题需扩模板及测试，而不是放宽执行器。独立图谱角色可能有更强的表面“人格感”，却会复制会话、工具门禁、记忆与结果出口，并与既有拓扑冻结冲突。

本 ADR 规定目标接口；具体图谱模板、授权和事件契约必须以运行测试验收，不能凭本文件声称 Graph B 已隔离。公共图谱当前只承诺已证明未混入用户写入的子集；被停用的旧接口需要迁移方案，不能把 HTTP 410 当作功能已补齐。

## Implementation note（2026-10-09）

代码新增只由离线审核种子发布的独立 `PublicSkill` 标签；公共技能模板、推荐与学习路径不再回退历史混存 `Skill`，未发布时明确 unavailable。此为上述决策的一个安全子集实现，不意味着岗位关系、个人图或历史数据已迁移。本机 Neo4j 当前连接拒绝，尚未执行真实发布和跨用户隔离验收；操作与验证见 `docs/runbooks/public-skill-partition.md`。
