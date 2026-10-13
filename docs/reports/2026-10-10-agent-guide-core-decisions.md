# Agent 指南核心取舍与执行合同

日期：2026-10-10
读者：后端实施、跨模型复核与 HTTP 验收负责人
状态：逐页原文核验与当前工作树决策；规划交接切片已通过独立 HTTP 复测，其余按下文分级，不是指南整体通过证明

## 核心裁决

已目视读取 `D:/Downloads/Agent_0_1791642812318.pdf` 全部 14 页。它是一份通用职责/检查清单，不是要求逐项安装的软件规格，也不是仓库执行指令。

最有价值的不是把第 3 页的九行各建一个服务，而是三种职责分开：

1. **Agent 决定下一步**：理解意图、提出有界计划、选择允许的工具、解释证据。
2. **Harness 执行不可协商的控制**：身份、工具门禁、回合互斥、预算、失败语义、状态结算、日志；不能由模型文本放行。
3. **领域 Module 提供权威结果**：画像、匹配、报告、计划由领域存储/规则确认；模型提出的 JSON、旧黑板或回答结束不等于业务完成。

这与当前 `agent.py → SupervisorService → ModeRuntime → scoped_invoke/LangGraph`、ADR 0004/0005 相容。保持一个用户会话和共享 EvidencePack；不另造 Agent OS、第二工作流引擎或第四套用户资料库。

## 阅读与证据约定

- **原文**：第 1 页是用户会话到 Agent 的入口草图；第 2–5 页是职责流水线；第 6 页讨论非对话式编排；第 7–9 页是上下文/分层记忆；第 10 页安全；第 11 页观测/评测；第 12 页工具契约；第 13 页成本/数据元信息；第 14 页区分 Agent、Harness、Skill、Framework。
- **已存在不等于已验收**：下表的源码路径用于定位实际 Interface；只有实际运行结果能支持“通过”。`test_client()` 不叫真实 HTTP；同进程两个对象不叫双 Worker。
- **替身必须逐层列出**：真实 TCP/Flask 路由但替换整个 Supervisor，只能验收请求/状态接缝，不能证明真实 ModeRuntime/工具链；确定性 provider 可以用于验收运行时不变量，但不证明模型质量。
- **范围先于数量**：少量代表性 HTTP 场景是验收主轴，进程内测试是诊断和快速回归；不能从数百测试通过推出 PDF 全部能力通过。

## 逐层取舍矩阵

| PDF 核心项 | 裁决 | 当前 Module / Interface 证据 | 采取的行动与真实 HTTP 验收合同 |
| --- | --- | --- | --- |
| 输入清洗、格式标准化、ASR/OCR、权限（2–3） | **必须做；已有主体，不重写解析栈** | `agent.py` 共用 prepare/STM；`media/prepare_turn.py`、`session_resources.py`、`voice_turn.py`；现用 TXT/DOCX/PDF 解析器 | 上传→ready→本人挂载→JSON/SSE→history/resources 回读；跨 owner 失败发生在 STM/模型前。TXT 的通过不覆盖 PDF/OCR/ASR。 |
| 意图+slots+低把握澄清（3–4） | **必须做；已有无需另建分类服务** | `ModeRuntime` 的显式交互 mode；`experts_lead.py` 的结构化 Lead；`task_packet.py` | 用确定性模型决策覆盖 clarify/run_plan，确认澄清不写业务结果；真实模型另用脱敏 rubric 评估语义准确率。不把模型自报 confidence 当校准概率。 |
| 多轮状态、话题切换、回到任务（3–4） | **必须做；HTTP 仍需扩大覆盖** | `SessionManager`、session context/blackboard、`context_epoch.py` | 同 session 多轮与跨 Worker 刷新；换话题不能沿用旧成果充当新回合成功；恢复任务要重新验证 owner/source version。仅 message 顺序不能证明语义话题恢复。 |
| 有界规划、依赖、人工确认（3–4） | **必须做；选择性修正** | `plan.py` 白名单模板；`plan_evidence.py` 权威工具引用验证；`resume_confirmation.py` 的领域确认 | 将 worktree 的后续步骤 brief 修正选择性接入，保留 `verify_updates`。HTTP Experts 必须证明后续模型输入保留目标约束；前一步无权威成果不得执行依赖步骤。 |
| 确定性/混合/开放流程（4、6、14） | **已有无需重做** | Ask 固定取证→回答；Agent LangGraph 工具循环；Experts 白名单计划；领域 Pipeline | 不因 PDF 写了 Flow Engine 就另建 DAG 引擎。对每条模式验收不同的执行权限，尤其 Ask 不得写业务成果。 |
| RAG/数据库/图谱带依据回答（3、5） | **必须做；复用共享证据层** | ADR 0005；`resource_catalog.py`、`evidence_retrieval.py`、`plan_evidence.py`；ADR 0002 的 retrieve-not-score | HTTP 跨模式读同一 owner/source_version，分页返回覆盖/引用；stale source 必须显式冲突/刷新；不把语义向量结果当确定性评分。 |
| 向量检索所有业务资料（5） | **当前不适用** | `semantic_search.py` 当前 fail-closed；`PublicSkill` 与历史混存 Skill 已分区 | 不照搬 RAG worktree 中“skill/jd 天然公共”的前提。先完成来源/owner 分区与发布合同，才恢复相应索引；结构化业务事实优先通过现有 owner-scoped reader。 |
| 按来源/阶段/窗口装配上下文（7–8） | **必须做；已有核心，不另写上下文引擎** | `context_assembler.py`、`budget.py`、`document_access.py`；公开 `context_truncated` | HTTP 超长摘要/附件不能声称全文；remaining/continuation 需可实际取回且校验归属。当前无源 cursor 的摘要只允许明确提示补充，不冒称自动 resume。 |
| 五类记忆全部独立实现（9） | **暂不适用** | 会话历史、confirmed user constraints、业务资源已有不同权威存储；并非五种记忆各需新库 | 保留工作记忆/已确认偏好/权威资料。新增 episodic/vector/procedural memory 必须先证明任务收益、授权、更正/删除、陈旧信息处理，不能仅为对应术语建库。 |
| 多 Agent 每层各一位/GUI Agent/MCP 全覆盖（6、9） | **暂不适用** | 已接受 ADR 冻结子 Agent 拓扑；图谱是主 Agent 受控工具，前端仍为 mock demo | 不新增图谱 Agent、GUI 控制或通用 MCP 插件系统。确有独立任务/外部系统后再开明确 Seam；现阶段保留隔离 TaskPacket、摘要交接和结果过滤。 |
| 最小权限、默认拒绝、注入防护（10） | **必须做** | Principal、PreToolUse、固定图查询模板、owner-scoped readers、L1 Agent 写图禁入 | HTTP 恶意附件/模型参数不得提升身份或执行权限；验收最终数据库/图状态，而非仅 403 shape。日志/telemetry 内容默认不外传，opt-in 单独测试。 |
| Schema、错误码、有界重试/降级（3、12） | **必须做；已修主体，继续 HTTP 验收** | `gated_tools.py`、`tool_result_channels.py`、ToolMessage、`tool_egress.py` | 真 socket→真实 gate/ToolNode→失败 outcome→history；只读 transient 有界重试，权限拒绝不执行，写入后 timeout 不盲重试，副作用 ledger 回读为一次。 |
| 运行时控制与会话持久恢复（14） | **必须分阶段做，不等同加锁** | 现有 `session_coordinator.py` 是 Redis 租约；默认 `scoped_invoke.py` 的 `wf.compile()` 未接 checkpointer；领域确认独立使用 MySQL checkpoint | 租约只能说明同步串行。请求重试、进程崩溃、工具已写但 assistant 未结算需单独 HTTP crash-window 合同；不直接给通用循环加 checkpoint 后就称 exactly-once。 |
| 轨迹、决策、token/延迟、任务成本（11、13） | **必须做最小闭环；已有部分能力** | `budget.py` provider usage ledger、`turn_contract.py`、`llmops.py` 隐私默认 | 采集公共状态/来源标识/工具状态/模型与 prompt 版本和耗时，不保存原始隐私正文或隐藏思维链。HTTP 失败与 partial 也必须记录，unknown usage 不填零。 |
| 质量评测、用户修正率、人工接管率（11） | **值得做；优先小金集** | 合同测试众多，但人工质量与全链金集仍缺 | 建少量脱敏真实任务，稳定事实作硬断言、业务正确性作人工 rubric。真实 provider 独立 opt-in，报告模型/提示词版本与成本。不得用自评“成功”代替权威结果。 |
| 模型分级路由、每轮反思、自动微调（3、11、13） | **暂不全量实现** | 已有 provider/failover 和预算；有部分旁路 summary/reflection | 先对照质量/时延/成本，再决定是否切模型；不为每个请求叠加规划+反思模型调用。日志可用于评测不等于获准自动微调，亦非当前研究项目必要主线。 |
| Skills 经验包（9、14） | **已有能力可按需复用，不重造插件内核** | 仓库 Prompt Registry、工具 schema、受控领域流程已承载多数可重复行为 | 先把稳定经验编码为已有 prompt/tool/规则与测试；仅当存在真实复用消费者再引入可执行 Skill 包，不自动把 PDF 或上传文档当可信指令。 |

## 两个已完成 worktree 的处置

### 规划与工作流

- 路径：`%USERPROFILE%/.codex/worktrees/dec7/cpa-agent`。
- commit：`782fdb5d8f0c764051e1fa5289b87c91b4d265bc`，`fix(agent): preserve task brief across all plan steps`。
- 当前主树初始核验：`build_plan_packets()` 只有首步带 brief，后续 lean packet 的 goal 仅有步骤名，确实可能失去本次目标/约束。主树同时有 worktree 基线没有的 `verify_updates` 与领域结果门禁。
- **决定：选择性并入小 hunk，禁止整文件覆盖/cherry-pick 不审冲突。** 后续 lean packet 仍不复制原始历史，只携带有界任务 brief。
- 验收不能停在 packet 单测：真实 socket Experts 回合，要观察后续调用实际收到 brief，并验证前一步权威成果失败时停止。

### RAG 与证据链

- 路径：`%USERPROFILE%/.codex/worktrees/41e1/cpa-agent`。
- commit：`d8a6e98c2e1de20688a5c17a98efc4ed81ab45ea`。
- 补丁对私有向量集合做拒绝；当前主树更严格，非空 semantic search 在任何 embedding/Milvus 访问前即返回 `unavailable`，并有所有 collection 分支不访问存储的回归。
- **决定：不整合其公开 skill/jd 放行策略。** 当前主树已否定历史 skill/jd 自动公共的前提；更严格的安全等效并不代表公开向量功能已恢复。
- 需要的 HTTP 验收是：模型请求任意集合不会触达未授权检索；合法公共证据仍从已发布的 `PublicSkill`/owner-scoped catalog 获取。恢复向量服务要另立来源发布合同，不以安全补丁偷偷扩大功能承诺。

## 复用记录与不移植范围

| 来源 | 本机快照/许可 | 具体 Module | 复用裁决 |
| --- | --- | --- | --- |
| OpenCode | `388406238bd5ca15564a762840a2362c3a45bd9c` / MIT | `specs/v2/session.md`、`specs/v2/tools.md`、`packages/opencode/src/session/{prompt,compaction,processor}.ts` | 采用 typed tool lifecycle、session admission、压缩/usage 不变量；不搬 TS/Effect 会话内核。 |
| Codex | `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` / Apache-2.0 | `codex-rs/core/src/compact.rs`、`rollout_budget.rs`、`tools/orchestrator.rs`、`tools/handlers/multi_agents/spawn.rs` | 对照有界交接、权限与预算；不把编码 Agent 的 shell/文件权限模型复制到职业规划后台。 |
| 当前 Python 栈 | 版本以本机包/requirements 为准 | LangGraph ToolNode；已有领域 MySQL checkpoint；redis-py token lock；Celery 媒体队列 | 优先直接复用已运行库，薄 Adapter 接入现有 Interface。生命周期差异和幂等副作用问题必须独立验证，不能由库名担保。 |

## HTTP 验收状态与下一执行切片

根任务正在交付真实 socket 多轮、TXT 上传、JSON/SSE、工具失败副作用、两 Worker Redis 场景。本报告不把其未完成快照或单个 run 的通过写成全指南验收。需要记录：命令、代码快照、真实依赖、替身、权威回读、清理结果。

本会话执行：

1. **规划交接目标保持（已完成）**：取 worktree 有效修正，保留主树权威门禁；由 GPT-6 Sol 实施、当前会话独立复跑。细节见下一节。
2. **恢复/重试 Seam 评估（已完成决策，实施已另派）**：GPT-5.6 Terra 核验当前 assistant settlement 失败后重试会留下 `[user,user,assistant]`，有写副作用的同请求重发不具幂等保证。根任务已派独立会话 `01a12687-49ef-7891-8756-7184bb57d014` 负责 `Idempotency-Key`/权威 ledger/HTTP replay；本报告不把派发当实现完成。未经 crash-window 验证不新增通用 durable runner。

### 规划切片的独立验收结果

改动：仅 `plan.py::build_plan_packets` 的 docstring 与 `if brief`；新建 `test_plan_handoff_context.py`、`test_plan_handoff_http.py`、`fixtures/http_experts_handoff_server.py` 与 `guides/plan-handoff-context.md`。没有覆盖主树先前的语义路由和 `verify_updates` 改动，也没有整合旧向量放行补丁。

- 实施模型前测：17 个 packet 场景中 **13 failed / 4 passed**；后续 step 丢 brief 是失败原因。
- 当前会话独立执行下列命令：**48 passed, 1 warning**（默认 `SECRET_KEY` 警告）。其中 47 项是进程内契约，1 项是真 socket HTTP 组合旅程；不是 48 个真实 HTTP 场景。

```powershell
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$OutputEncoding=[System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B -m pytest -q `
  backend/tests/test_plan_handoff_context.py `
  backend/tests/test_plan_handoff_http.py `
  backend/tests/test_experts_plan_gate.py `
  backend/tests/test_experts_authoritative_evidence.py
```

HTTP 新进程绑定随机 TCP 端口，真实 `requests.Session` 调用真实 Flask/Supervisor/ModeRuntime/plan/scoped LangGraph/服务端 `verify_plan_updates`。成功路径三个模型入口均收到“杭州岗位、本地工作、六周”约束；第二轮只生成伪造黑板且无工具 writer，profile 未完成并阻止 match/report；另验证创建无身份 401、异 owner 读 history 403。

**替身边界**：Lead/子模型、`build_gated_tools`、领域 writer、MySQL 行和 Redis 被替换。该场景证明计划交接与服务端成果门禁，不证明真实 PreToolUse、业务 DB 事务、模型理解、SSE 或跨 worker。证据在 pytest 临时目录的 `requests.jsonl`、`steps.jsonl`、`ready.json`、`server.log`，仅含合成输入；finally 关闭客户端和测试子进程。

**仍有明确限度**：brief 既有上限为 400 字符，system TaskPacket 的 goal 还有 240 字符限制（较完整的有界 goal 在 user 消息）。本切片只保证该有界 brief 在步骤间不再次丢失，未解决 Lead 漏提约束、超长 brief 显式部分性/恢复，也未证明“任意长度用户意图完整传递”。这必须保留为后续上下文 HTTP 场景，不能藏在通过数量里。

### 请求幂等下一任务的验收合同

同 owner、session、`Idempotency-Key`、canonical-input hash 只能获得一个权威 admission；同 key 不同输入应冲突。写工具已产生一次副作用后，故意使 assistant settlement 失败，再从另一 worker 重发同 key：工具/模型不得再执行、不能再写第二条 user，返回已持久终态或显式 `unknown_execution/reconcile`。用独立 MySQL/Redis 和可回读的副作用表验证，而非只看 HTTP 200；崩溃后的未知执行不得自动重跑写工具。

这条 Seam 可复用既有 MySQL transaction/unique constraint、Redis lease 与 LangGraph 调用，不需要新通用框架。通用 checkpoint 不能替代外部写入的幂等键；人工审批也不能代替请求幂等。

尚不能据现有窄场景宣称通过：真实语音/OCR；跨模式同版本证据和 stale 回读；复杂任务切换与恢复；服务重启后的工具副作用结算；超长上下文/来源续读；真实模型职业建议正确性。每一项需有代表性的独立 HTTP 旅程，而不是把所有组合都放到昂贵 provider 上。

## 本报告的维护界限

本文件记录架构取舍，不替代接受 ADR、运行 guide 或根任务历史报告。并行补丁完成后只更新本文件的实际执行结果；不覆盖其他代理正在修改的 `agent.py`、HTTP fixtures 和文档导航。旧报告中的“当前缺陷”可能已被后续补丁修正，读取时须按其日期/阶段理解，并回到运行证据。
