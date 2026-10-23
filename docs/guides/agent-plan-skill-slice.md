# Agent 计划与 Skill 接入切片

**状态：部分实现；增量更新至 2026-10-18。**本文记录代码当前增加的单请求内执行路径，不代表 AI 工作台的持久 run、取消/接续或完整真实服务验收已经完成。下文 2026-10-17 的回放是当时的历史证据，不能用来描述后续代码的运行结果。

## 已接入的窄路径

```text
Agent / Experts 消息
  → 主 Agent 意图与计划决策
  → 服务端校验顺序步骤（最多 6 步、最多 3 个不同角色）
  → TaskPacket 角色/Skill 白名单
  → 逐步调用既有子 Agent / LangGraph scoped tool executor
  → 现有业务回读门禁确认步骤结果
  → 主 Agent 汇总已执行步骤的输出
  → turn egress 公开实际步骤状态
```

- `Ask` 仍走直接问答，不经过 Agent 计划执行器。
- Agent 和旧 Experts 入口当前分别保留意图处理，但执行 `run_plan` 时共用 `ModeRuntime._execute_planned_steps`。这不是持久化 run API，也不证明现有所有旧入口均已迁移到统一执行器。
- 动态计划的角色必须来自现有子 Agent 白名单；步骤串行执行，工具权限由后端策略决定。用户/模型不能通过步骤自带字段增加工具、权限、依赖图或并行能力。
- 老模板 ID 仅作为受限兼容输入，仍会展开为服务端既有白名单模板；任意 template 不会被接受。
- 每一步失败后 `run_plan` 停止后续步骤，之前已确认的业务写入不会被统一回滚。业务成果只在现有验证器读取到符合 owner、状态和本轮来源条件的记录后计为完成。

## Skill 渐进加载

目前仓库内置一个 `school-context` Skill，位于 `backend/config/agent_skills/school-context/SKILL.md`：

1. 主 Agent 规划只接收 Skill 的短元数据卡片。
2. 服务端只允许 `qa` 角色选择 `school-context`；Skill 正文在步骤校验后、进入受限子 Agent 前按需读取。
   `SKILL.md` 采用 Agent Skills 标准的 `name` / `description` frontmatter 与 `metadata.version`；运行加载时核对目录、角色、注册表版本和描述，不符合则拒绝使用。
3. 子 Agent 请求包含 Skill 内容摘要，步骤结果回传 Skill ID、版本和摘要。
4. Skill 是只读解释方法，不运行随包脚本，不修改 D1 学校评分、画像或正式业务状态。

**当前窄边界：**`school-context@1.0.2` 被选中时，执行侧会读取当前 owner 的已确认简历和教育部历史 211 正向名单；精确命中附来源，未命中保持未知。985/双一流、别名、当前学校身份及 D1 正式评分均不由此断定。详见 [院校事实源切片](school-fact-source-slice.md)与[院校背景解读设计](../specs/school-context-skill.md)。

持久 run 另有一个结构化显式绑定：`expert_id=career-coach` + `capability_id=career-planning`（capability ID 稳定，发布版本独立固定），约束 Lead 只提出 1–6 个串行只读 `qa` 步骤，且每一步必须使用 `school-context` Skill。Lead 接收权威服务端绑定，负责意图/计划；它可标记不适用或要求澄清，但不得改选其它包。服务端拒绝而不修补遗漏 Skill、错角色或错版本的计划，并对明确不适用请求提示切换。未选择能力包的旧 Experts 仍走既有 Lead 行为；Ask 直接问答不经持久规划器。此实现尚不等价于真实模型/owner 资料读取及 D1 前后不变验收。

## 回复与过程展示

- 已移除 Agent/Experts 输出合成第一人称“思考过程”的路径；`thinking_steps` 当前为空，不能将其当作真实模型思维或完整执行日志。
- 子 Agent 输出用于步骤结果与校验；若最终合成器不可用，回退仅使用已完成步骤的安全摘要，或明确报告步骤未完成。
- 前端进度卡展示本轮响应中实际发生的步骤状态。它不是数据库事件日志，不支持游标续读、刷新恢复，也不等于跨 worker 的持久运行状态。

## Prompt 版本证据

Prompt Registry 现在能计算生效 base/tone 内容摘要，并把摘要纳入选择缓存键与上下文 epoch 比较；提示选择元数据含 prompt 内容 SHA-256。它解决“只看版本号忽略内容变更”的追溯缺口，但当前没有把 Prompt、Skill、模型配置三者的完整发布清单固定到一个持久 run，也没有实现运行中热切版本控制/回滚 API。

## 尚未实现或未验收

- Owner-bound MySQL run/计划版本/步骤/事件 schema、submit/status/events/cancel HTTP façade、MySQL outbox 与 `cpa.agent` worker 已添加。QA-only 持久执行器已接 Lead 计划、服务端白名单验证、串行 QA 步骤和 MySQL 最终答复；一次隔离真实 HTTP→MySQL→Redis/Celery→qwen-plus→MySQL replay 完成。该次没有触发 Skill 或工具，不等于其他角色/资料读取通过；详见 [Agent 持久运行状态层](agent-run-persistence.md) 与[隔离回放报告](../reports/2026-10-18-agent-run-live-replay.md)。
- pre-claim 取消与已完成 run 重复 transport 已由真实隔离 worker 验证：取消先到保持 `cancelled` 且消费消息不追加事件，completed 重复消息不改结果/游标。运行中中断、写入竞态、worker 执行步骤中的崩溃恢复仍需专项核对；新意图接续及刷新后页面恢复也未实现。
- 持久事件序号、owner-bound HTTP `after_seq` 续读方法已有 SQLite API 测试与独立 MySQL 状态 smoke，但没有 SSE/页面游标、断线重连、前端接线或刷新后 UI run ID 回读。
- 最后一个子 Agent 步骤完成只进入 `steps_completed`；主 Agent 最终答复/结果引用经事务持久化后才是 `completed`。该阶段已在本次真实 run 中通过，MySQL `run.completed` 与最终答复哈希同事务落库；仍未覆盖多步/中途重启。
- 本地回归覆盖已绑定的一个显式专家包；其他专家/能力组合、真实 HTTP→模型→已确认院校资料→Skill 调用及 D1 业务记录前后不变仍未验收。不能据此宣称整个专家目录或页面流程已完成。
- Ask/Agent/Experts 前端流程的 HTTP 端到端复验、真实模型稳定性/自然度和页面观感验收。
- 211 历史名单已作为按需只读参考接入并通过假数据单测；仍需真实 owner/MySQL/模型的正反例、Skill 调用前后 D1 相关记录不变的真库验收，以及整个 run 的版本固定。

因此当前自动化测试证明计划校验、按需加载、步骤编排、HTTP 状态 API 和 SQLite run 状态转换；另有隔离 MySQL migrations/repository、真实 QA-only worker/model replay、资料 unavailable 语义和 worker 重复/取消边界证据。浏览器全链路、专家包/附件、真实 Skill/业务资料工具、写步骤和默认开发库 `agent_action_plan` 缺表现象仍未验收或解决。

## 2026-10-17 真实路由尝试（部分失败）

用 `Principal('diag-synthetic-20261017', 'diag-synthetic-20261017', source='test_auth')`，通过 Flask `test_client` 请求真实 `POST /api/agent/chat`，`mode=supervisor`、`interaction_mode=agent`。复用了合成简历：

> 合成简历：张同学，某普通本科计算机专业。项目：用 Python 写过一个课程推荐演示，负责接口和数据清洗；会 SQL、Git。无实习。

为避免业务写入，问题改为只读院校标签判断（与旧基线的“两个求职短板和下一步建议”不同）。本次 HTTP `200`，公开结果是 `needs_input`，Lead 返回 `action=clarify`、`source=lead_unavailable`，没有 steps、子 Agent 或 Skill；公开回复说明规划服务暂不可用、尚未启动步骤。`thinking_steps=[]`；过程卡只含 `clarify`、空的缺口和计划草案字段，未出现合成第一人称思考过程。

首次请求的阻断点为 provider usage：当时 `lead_classify` 选择 `qwen-turbo` 并使用 JSON 输出，运行日志报 `turn_token_usage_unverified`，之后按既有 fail-closed 预算门禁停止。为区分路由和门禁，保持请求、Principal、JSON 输出与其余路由参数不变，仅将 Lead 模型改为 `qwen-plus` 做了一次探针：该请求返回可核验的 provider usage，但尚未启动步骤，因 Lead 给只读 QA 计划附加了无关的 `resume`、`portrait`、`match`、`target_role` 缺口。此后将 Lead 默认路由显式切换为 `qwen-plus`，并修正动态计划门禁：执行计划的缺口由服务端对校验后角色推导，模型额外列出的缺口不再阻断窄只读计划；明确的 `clarify` 决策和 profile 等确实需要简历的服务端条件仍然生效。`qwen-plus` 的单位成本高于 `qwen-turbo`；本改动保留 700 token 上限、0.1 temperature 和现有预算门禁，不配置自动降级，也不声称已验证价格或所有 provider 场景。此次没有修改 `budget.py`/`LLMClient` 门禁。独立的只读业务上下文查询报告 `cpa_agent.agent_action_plan` 表缺失。

此诊断只屏蔽了 `SessionManager` 的 conversation/message MySQL 写入，并使用真实 Redis 建立并删除唯一临时会话；没有执行 Alembic 或手工迁移。`create_app()` 的启动日志报告媒体与 `tool_audit_event` schema 已确保，但未做数据库前后快照，故不声称数据库 schema 完全未变化。它是 in-process Flask `test_client` HTTP 路由验证，不是独立 socket 服务或浏览器验收；真实模型下尚未执行到子 Agent/Skill/最终合成步骤。

## 2026-10-17 修正后的只读 Agent HTTP 回放

按上一轮 `qwen-plus` 探针的同一合成简历与只读问题，使用默认路由（不再临时 monkeypatch 模型）从真实 Flask `POST /api/agent/chat` 回放一次。请求状态为 `200`，Lead 为 `action=run_plan`、`source=llm`，服务端校验后计划为一个 `qa` 步骤并选中 `school-context`；`missing_slots=[]`。子 Agent 实际输出后返回 Skill 版本 `1.0.0`、SHA-256 `5a8d18bf23a7deea5b7cf92aa9ccc373d6162decfe633601233e023d0fd3295c`，无工具调用、无权限拒绝。主 Agent 生成了公开回复；`thinking_steps=[]`，前端 parts 中有“本轮实际执行步骤”、`资料问答 completed` 的真实 progress 卡，而不是合成思考文本。

公开答复将校名与官方院校标签保持未知，依据只限于用户给的“某普通本科计算机专业”文本。此验证没有接入名单或外部院校事实源，也没有证明院校标签正确性；更没有用数据库前后快照证明 D1 业务行未变。usage ledger 为 `verified=true`，本次 qwen-plus Lead、LangGraph 子步骤、主 Agent 最终合成用量分别为 1,492、5,839、1,167 tokens（合计 8,498；为这次响应的观测值，不是成本报价），预算上限 30,000 未放宽。后台 `rollup_summary` 工作因 accounting unavailable 被跳过，不影响本次响应，但异步摘要接续并未验收。

回放仍观察到只读证据装载查询引用缺失的 `agent_action_plan` 表；未运行迁移。该表错误没有阻断本次 QA + Skill 回答，但意味着依赖持久计划数据的上下文能力尚不可用。与前一请求相同，本次屏蔽 conversation/message MySQL 写入、用真实 Redis 建立并清理唯一会话；应用启动仍报告媒体和 `tool_audit_event` schema 已确保，未做 schema 前后快照，故不宣称数据库完全无副作用。这是受隔离 Principal 的 in-process Flask HTTP 路由验收，不是独立 socket/浏览器测试，也不证明 owner-bound 持久 run、取消或接续能力。

### 回放输入与隔离条件

本次最后回放使用的完整输入为以下两段直接以换行拼接；没有省略或改写：

```text
合成简历：张同学，某普通本科计算机专业。项目：用 Python 写过一个课程推荐演示，负责接口和数据清洗；会 SQL、Git。无实习。
请只读解释这里的学历/学校标签能确认到哪一步，最重要的资料缺口是什么。只基于这段文本，不要修改资料、评分或生成正式计划；未知就说明未知。
```

请求为 `POST /api/agent/chat`，JSON 为 `{"message": "<上面两段以一个换行符连接>", "mode": "supervisor", "interaction_mode": "agent"}`；不传 `session_id` 或 `use_memory`，由路由创建新会话并采用默认 memory 行为。Flask `before_request` 注入 `Principal("diag-synthetic-20261017", "diag-synthetic-20261017", source="test_auth")`。

为避免此诊断在持久会话表写入记录，`SessionManager._save_session_to_mysql`、`_save_message_to_mysql`、`_update_mysql_title` 被临时替换为 no-op；会话状态仍使用真实 Redis，响应结束后按返回的 `session_id` 调用 `delete_session` 清理。`mode_runtime.experts_lead_decide` 和 `_invoke_subagent` 仅被包裹以旁路记录其原始返回值，并委托给原实现；未替换模型路由、LLM、LangGraph/scoped executor、Skill loader 或 Redis/MySQL 读取。因而本次 Lead 默认使用代码中现有的 `qwen-plus` 路由。应用启动期间已有的 schema ensure 行为未屏蔽。

**可复现性限制：**精确输入和隔离条件已记录，但当时没有保存可直接执行的一键回放脚本；模型输出具有非确定性，本地 DB 仍缺 `agent_action_plan` 表，且应用启动会执行 schema ensure。故这些记录足以复核输入、身份、路由和隔离策略，但不能保证在未恢复相同运行依赖/数据库状态时逐字重现回复，也不能替代干净数据库上的独立回放。
