# AI Native 职业规划平台：主 Agent 调度与交互收束方案

Status: working specification（会话主 Agent 子系统；不是已全部实现的运行能力）
Date: 2026-10-09

> 本文最初聚焦会话主 Agent。平台同时有功能页内的画像、匹配、报告、成长分析、推荐、图谱、KB 等 AI/Agent 入口；整体边界、重复执行器与开源选型以 `docs/architecture/platform-agent-capability-architecture.md` 为上层工作方案。不能用本文的聊天链路替代整个平台设计。

## 已确认的产品选择

用户确认：**图谱是同一主 Agent 可调用的能力，不是另一个独立对话 Agent**。保持 `AGENTS.md` 的开源复用硬规则、ADR 0002 的拓扑冻结、ADR 0004 的公共图谱证据范围。职业画像、匹配、报告、课程/项目/网站推荐是领域任务；主 Agent 负责澄清、调度、证据整合和对用户解释，不把业务打分交给语言模型。

“AI Native”在此不是给每页加聊天框，而是每个重要产品动作都可回答：当前用户意图、所用证据、要执行的工具、权限/预算、任务状态、可恢复位置、结果成果物、下一步可行动建议。网页或项目推荐未来需要可追溯来源与时效标记，不能让模型凭记忆生成虚构链接。

## 先承认当前运行面

| 当前可达路径 | 事实 | 不应误称 |
| --- | --- | --- |
| `/api/agent/chat`、`/chat/stream` → `SupervisorService` → `ModeRuntime` → `scoped_invoke` | Ask/Agent/Experts 主入口；工具回合用 LangGraph `StateGraph`/`ToolNode` 与现有 PreToolUse、双通道出口。 | `session_runner.py`/`agent_loop_integration.py` 虽有实现，但目前未接入此主链，不能把其中 steer/queue/恢复功能当已交付。 |
| `GraphQueryTool` | 现有 qa/planner 等通过工具白名单调用；属于同一主 Agent 的领域证据工具。当前只执行 `CareerNode` 岗位列表/路径固定模板，技能查询明确不可用。 | `subagent_graphs.py` 的 `graph:` 标签指 LangGraph 子任务，不是独立知识图谱 Agent；共享 `Skill` 混有用户简历/JD 写入。 |
| `/api/graph` | 图谱页面查询接口，与 Agent 工具不同。当前公共查询仅留 `career_path`，旧 Skill/JobPortrait/整库 overview 类路由返回 410；构建入口绑定 Principal。 | 410 是安全降级，不是职业图谱功能完整。不能因为查询是 read-only 就声称用户数据安全。 |
| `/api/roles/chat` | 路由已注册，但旧 `RoleRuntime` 向同步 `ModeRuntime.chat` 传不存在的参数，原本实测 HTTP 500；现明确返回 501，避免把故障伪装成可用能力。 | 角色 YAML/前端 mock 不是已接入主 Agent 的“专家会话”。 |
| `fork_jobs` | 进程内任务线程；OCR 状态可见但重启不自动恢复。 | 不能称为持久任务系统。 |

## 目标数据流：一条主 Agent 回合，领域能力按需进入

```text
可信 Principal + session_id + 用户多模态输入
  → 规范化 TurnInput（文本/附件/语音/图片引用；文件解析状态）
  → ContextAssembler（本会话 STM、选择性记忆、证据指针、模型预算）
  → 主 Agent 决策（回答 / 澄清 / 检索 / 执行已授权任务）
  → PreToolUse（工具可见性、参数/身份、幂等、权限）
  → 领域工具：Document Access / 公共图谱证据 / 岗位与项目推荐 / 画像匹配报告
  → Evidence Envelope（来源、作用域、覆盖、时效、是否完整）
  → 主 Agent 结论 + PublicTurn（正文、引用、进度、待用户输入、成果物）
```

图谱不是每轮必查：先判定问题需不需要岗位关系、技能先修或职业路径证据；当前只对已隔离的 `CareerNode` 路径取最小结果。技能图谱因 `:Skill` 混有用户写入而暂不查询；涉及用户简历图时未来另走 owner-scoped 查询，不把 `Person` 混入公共工具。图谱检索结果、向量召回和网页推荐都属于**证据**，匹配分仍由规则/代码计算。

### 要做深的 Module / Seam

| Module | 当前 Interface 摩擦 | 建议深化的 Seam 与 Leverage |
| --- | --- | --- |
| Turn Outcome | `reply`、SSE 事件、工具状态、异步任务各自表达“完成”；部分结果可能被文案掩盖。 | 一个 `TurnOutcome` Interface：`status`（completed / needs_input / running / partial / failed / cancelled）、`turn_id`、`resume/cancel` 能力、`evidence_refs`、`artifact_refs`、`next_action`。HTTP/SSE/history 共用投影，测试通过同一 Seam。 |
| Evidence Access | 附件、图谱、向量、网页结果形状各异；“找到数据”与“可引用的证据”混在一起。 | 一个最小 `EvidenceEnvelope` Interface，包含 source/type/owner_scope/as_of/coverage/complete/citation_target；各领域 Adapter 不把原始数据库行直接塞给模型或 UI。先有两种实际 Adapter 再抽象，避免空接口。 |
| Tool Execution | 模型工具调用、PreToolUse、LangGraph ToolNode、UI card、审计之间的结果语义可能分叉。 | 保留现有 gated-tool Seam，统一成功/拒绝/失败/部分完成的结构化结果；不要以消息中文关键词推断工具是否失败。公共图谱 Adapter 只执行固定模板。 |
| Long-running Work | `fork_jobs` 线程和 OCR/报告状态不耐重启，取消与恢复缺权威游标。 | 优先使用已有 MinerU 异步任务能力或成熟持久队列；会话内需暂停用户确认时评估 LangGraph checkpointer + `interrupt/Command(resume)`，不继续扩写本地 SessionRunner。 |
| Role Presentation | 单独 `/api/roles/chat` 复制身份和会话流，且原本实际 500。 | 角色首先是 Prompt Bundle 的受限语气/专业视角 Adapter，而不是新 subagent；接同一 `TurnOutcome` 和会话后再开放。不得由 YAML 的 `tools` 字段绕开工具白名单。 |

删除测试：若删掉一个 Module，只是把状态/权限/预算细节散回多个调用方，它有 Depth；若删掉 `session_runner.py` 主链毫无变化，就不应以其存在证明产品能力。

## 开源选型（按已运行版本，不盲目换底座）

1. **LangGraph 继续做会话内工具编排。** 本机 `mkg` 是 1.2.9，已有真实 `StateGraph`/`ToolNode` 主链。下一步复用其 checkpointer、interrupt/Command 和 stream 能力，而不是再写第二套循环；官方文档说明 checkpointer 用于会话恢复，interrupt 在等待用户输入时保存状态。1.2.12 才有新版 typed interrupt response schema，当前环境不能直接假设可用。[持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、[中断](https://docs.langchain.com/oss/python/langgraph/interrupts)、[流式](https://docs.langchain.com/oss/python/langgraph/streaming)。
2. **OpenCode/Codex 作可核对的契约参考，不整仓移植。** 本地快照、许可证、模块与适配边界见 `docs/architecture/agent-upstream-adaptation.md`。尤其参考显式停止/部分完成、文件分页、模型预算；未经主链调用证明的平行 Python 仿制实现应先隔离而非继续生长。
3. **MCP 仅用于将来跨产品工具互联的 Seam。** 内部现有 Python 工具不必先全部套一层 MCP；如果接 WorkBuddy/Qoder 等外部工具，用官方 SDK/协议版本的工具 schema、身份透传和异步任务能力，不把 MCP 当权限系统。2026-07-28 协议已发布，但部署前还须做版本协商和端到端兼容测试。[MCP 官方发布说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)。
4. **多模态解析继续用现成解析器。** OCR/版面、DOCX、语音、未来图片理解各有 Adapter，保留原始文件与派生证据；“提取覆盖”与“理解正确”分开评分。不要把所有模态强塞进单个长 prompt。

## 用户交互契约应先覆盖的真实问题

### 提示词不是权限边界：保持可测试的分层

当前运行时已有 Prompt Registry、`PromptSelector`、Ask/Agent/Experts 模式提示和子任务 TaskPacket，不应再添加一份会抢优先级的“万能超级提示词”。建议固定以下装配顺序，并给每层记录 prompt ID/版本与输入证据：

1. **不变量层**：身份/owner、工具白名单、文件完整性、证据与打分边界；由代码门禁执行，提示词只解释给模型。
2. **模式层**：Ask 以回答/澄清为先；Agent 可选择受控工具；Experts 只在预定义计划和每步权限范围内调用。自然对话也必须能直接回答，不能为“Agent 感”强行调用工具。
3. **专业视角层**：未来职业教练、简历顾问等只改变提问方式、解释深度和输出风格，不引入第二身份、第二记忆或隐藏工具授权；暂不把未接线的角色 YAML 宣称可用。
4. **任务层**：目标、缺槽、相关附件/图谱证据指针、允许写回的字段；用户上传内容、网页、图谱节点和工具结果均作为**不可信数据**，不能覆盖前几层指令。
5. **出口层**：模型先给依据、假设和行动建议；公开投影再统一做引用、部分完成、成果物和事件。不得靠“请一定不要”一句提示词代替工具 gate 或 URL 校验。

职业规划效果要用分任务黄金集评估：简历问答是否用了全文、缺条件时是否只问必要问题、图谱不可用时是否如实降级、推荐是否有可打开且时效明确的来源、规则分是否未被语言模型改写。每次回放记录模型、提示词版本、附件覆盖、工具输入/结果和人工判分；先固定一条真实任务链再扩充模板。

- **澄清**：缺目标岗位、地点、学历证据、JD 时给最少必要问题；不能同时默默执行画像或推荐。问答要有 `needs_input`，不靠回复文案猜。
- **可见进度**：长任务展示 queued/running/waiting/completed/failed；后台 OCR 未就绪时保留原文本但注明局限，支持 retry。SSE 重连需按 `turn_id`/事件序号去重，不重新触发写工具。
- **工具许可**：写画像/报告、外部项目推荐跳转、用户数据访问分别有可见门禁；拒绝或未执行要如实显示，不得算完成。
- **证据**：每条关键建议能指向简历段落、岗位要求、图谱关系或来源网站；“推测”单独标记。引用 URL 必须经过公开投影规范化。
- **角色**：专家视角可改变提问风格和解释侧重，但不能改变身份、数据作用域、业务规则分或工具权限。
- **失败与恢复**：模型达到工具步数上限、工具报错、网络断流、用户取消时保留已完成/未完成清单和可继续位置。

## 分阶段验收（严格区分实现与目标）

| 阶段 | 必须经过的端到端用例 | 当前状态 |
| --- | --- | --- |
| P0：主链诚实性 | 第 6 次工具请求不执行 → `partial` 可见 → 不写“完成”；危险引用不出站；strict 无身份为 401。 | 窄代码/契约测试在本轮推进；公共 `TurnOutcome` 尚未统一。 |
| P0：公共图谱证据 | 主 Agent 自然语言请求公共职业路径 → 固定模板参数化查询 → 返回标量与明确覆盖范围；任何 `Person`/无公共标记 `Skill` 查询负向拒绝。 | `CareerNode` 列表/路径与 Flask 公共路径已用替身测试；共享 `Skill` 等读面故意 410，真 Neo4j 和证据来源引用仍未验收。 |
| P1：有状态用户交互 | Ask 澄清 → 用户补充 → Agent 获授权工具 → 异步进度/断线恢复 → 可追溯结果。 | 未完成。需持久化和统一 Turn Outcome；不可称为现有前端能力。 |
| P1：角色接主链 | 同一会话切换职业教练解释风格，身份/历史/工具权限不变；角色配置失效不影响基础问答。 | `/api/roles/chat` 暂明确 501，前端仍为 mock；需统一运行时后再接。 |
| P2：AI Native 推荐 | 简历+目标岗位+图谱/实时网页证据 → 项目/课程/网站候选 → 时效/来源/适配理由 → 用户反馈回写。 | 尚无完整可达链路与黄金评测集。 |

本规格不以增加 agent 数量解决质量问题。每一阶段应从一条实际 API 输入到权威结果表/公开事件复验，记录模型、提示词版本、工具调用和人工评分；自动测试只能证明契约，不能替代职业规划效果评估。

## 本轮验证快照

- 在 `mkg` 运行时对启动、Prompt Registry、主 Agent 工具上限、隔离、HTTP/SSE 引用、角色路由、开发身份门禁及图谱公共接口执行联合回归：`87 passed`（模拟模型/数据库的契约测试）。
- `pytest backend/tests --maxfail=3`：`333 passed, 3 failed`。前两项 `test_hybrid_retrieve_explain.py` 因本地 Neo4j `localhost:17687` 未启动而连接拒绝；第三项 `test_workspace_loaders.py::test_load_branding` 期待 `CPA-Agent Demo`，但当前脏工作树的 `brand.yaml` 是 `AstroGraph`。这些不是本轮 Agent 主链联测的绿灯，也不应通过修改无关品牌数据来掩盖。
- 未做真实 Neo4j/主模型/浏览器三方联调；OCR 准确率是用户已检查的输入前提，不据此推断主模型必然正确使用全文。
