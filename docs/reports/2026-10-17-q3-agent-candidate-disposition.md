# Q3：22 个未跟踪 Agent 候选逐项取舍（2026-10-17）

**结论：22/22 继续隔离，0 个接入、0 个删除或移动。**这是逐文件的当前处理结论，不证明作者、许可证或未来永无用途；没有一个候选能从已复现的默认请求缺陷、现有实现缺口和上游复用边界三者同时获得接入理由。尤其两份归一化代码会自动写仓库权威 YAML，一份“Durable”事件总线只存进程内列表，一份工具 schema 对输出恒返回 `True`，一份文件路径检查使用字符串前缀；这些不能作为现行业务能力接线。

## 核查口径与上游快照

- 从 `git ls-files --others --exclude-standard -- backend/app/services/agent_runtime backend/app/utils` 得到 22 个 Python 文件；逐个 AST 读取入口/类/方法并检查关键状态与副作用。项目跟踪源码的默认 `/api/agent/chat` 经 `agent_runtime/__init__.py` 进入 `supervisor.py`；[此前 HTTP 回放](2026-10-17-untracked-agent-modules-triage.md)用确定性 Supervisor 与存储替身完成 1 条多轮链，但不是真模型验收。本轮[Q1 干净镜像](2026-10-17-q1-candidate-runtime-replay.md)排除这 22 件后，Gunicorn、正式账号和另一 owner-bound HTTP 写入仍能启动/完成；这只能证明该窄链不依赖它们，不能证明所有动态分支不可达。
- 上游对照是本机 OpenCode **`388406238bd5ca15564a762840a2362c3a45bd9c`（MIT）**的 `packages/opencode/src/` 和 Codex **`a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0）**的 `codex-rs/core/src/`，详见[既有适配记录](../architecture/agent-upstream-adaptation.md)。这两个仓库是参考快照，均**不是** CPA-Agent 运行依赖。表中“上游”是可对照的成熟模块/契约，不意味着直接移植 TypeScript/Rust 内核；简历领域判断没有同构的通用上游。
- 下表路径以 `backend/app/` 为根，决议“隔离”表示原文件保留未跟踪、无 runtime 接线、无构建纳入；“禁止原样接线”是额外的实现风险标签，不授权删除。没有已复现默认 Agent 主链的具体缺陷需要这组候选修复，因此本轮不启动 Agent 基础能力改造，也不制造薄适配代码。

| 候选文件 | 对照的现行边界 | 可用上游对照与当前取舍依据 | 决议 |
|---|---|---|---|
| `services/agent_runtime/agent_loop_integration.py` | `supervisor.py`、`resume_confirmation.py`、现行 L0 流程 | OpenCode `session/processor.ts` / `tool/registry.ts` 是完整回合/工具边界；此文件另建简历工具循环，无当前路由接线或 owner-bound 成果回放。 | 隔离 |
| `services/agent_runtime/session_compaction.py` | `budget.py`、`compaction.py`、`context_assembler.py` | OpenCode `session/overflow.ts` / `session/compaction.ts` 按模型窗口和实际用量判断；候选默认 `context_limit=12000` 且 `len(content)*2` 估 token，未处理真实 provider 窗口、非文本与完整性合同。 | 禁止原样接线 |
| `services/agent_runtime/session_input.py` | `session_coordinator.py` 的 Redis lease、`turn_idempotency.py` 的 MySQL 账本 | OpenCode `session/run-state.ts` / `session/prompt.ts` 提供输入与运行状态参考；候选仅 `threading.Lock` + 进程内 `_inputs`，跨 Gunicorn worker 和重启不可重放。 | 禁止原样接线 |
| `services/agent_runtime/session_runner.py` | `supervisor.py`、LangGraph checkpoint、`turn_contract.py` | OpenCode `session/processor.ts`；候选又建一条 `run/_run_turn/_execute_tools` 状态机，未证明可保留现行请求幂等、工具状态和部分结果。 | 隔离 |
| `services/agent_runtime/tool_registry.py` | `tool_factory.py`、`tool_policy.py`、`gated_tools.py` | OpenCode `tool/registry.ts`；候选有独立进程内注册和 materialize/settle，未接入现行 Principal、审计和工具结果通道。 | 隔离 |
| `utils/agent_loop_v2.py` | `supervisor.py`、`resume_confirmation.py` 与 L0 任务状态 | OpenCode `session/processor.ts` 可参考通用回合；此文件另建 ResumeAgentLoop，不能以“V2”名称替代当前持久任务/证据流程。 | 隔离 |
| `utils/compaction.py` | `agent_runtime/compaction.py`、`budget.py` | OpenCode `session/compaction.ts` 与 Codex `compact.rs`；候选摘要 `_cache` 是进程内 dict，规则摘要与 LLM 摘要未证明超预算后仍显式标记、可继续读取。 | 隔离 |
| `utils/concurrency.py` | `session_coordinator.py`、Celery/Redis 任务队列 | OpenCode `session/run-state.ts` 可比较并发状态；候选 `asyncio.Lock`、Semaphore、TaskGroup 仅单进程，不补现行跨进程请求串行或任务恢复。 | 隔离 |
| `utils/context_source.py` | `context_assembler.py`、`prompt_selector.py`、`resource_catalog.py` | OpenCode `session/system.ts`；候选是可变进程内 SystemContext registry，未证明来源版本、Principal 归属或证据截断。 | 隔离 |
| `utils/errors.py` | 现有 HTTP `ScopeError`/安全错误响应、工具失败通道 | OpenCode `session/message-error.ts`；候选自建错误标签和 ErrorHandler，未见默认链路需替换的已复现错误，也未接现行 fail-closed 语义。 | 隔离 |
| `utils/event_sourcing.py` | `turn_idempotency.py`、MySQL Pipeline 事件账本 | OpenCode `storage/schema.ts` / `bus/global.ts` 将存储与通知分开；候选虽名 `DurableEvent`，`EventBus._event_store` 实为进程内列表且超过 10000 会截掉前半，不能当持久事件来源。 | 禁止原样接线 |
| `utils/file_utils.py` | `media/document_access.py`、`media/file_store.py` | OpenCode `tool/read.ts` / `tool/truncate.ts` 显式 offset/截断；候选 `safe_path_join` 用路径字符串前缀，`read_file_content` 只读至 2000 行且无 `has_more`，不能作为受限文件读取边界。 | 禁止原样接线 |
| `utils/mcp_adapter.py` | 当前 `tool_factory.py` 的显式注册（无 live MCP 合同） | OpenCode `mcp/index.ts` 是更完整的协议/生命周期对照；候选仅自行按行发送 JSON-RPC、固定 ID，HTTP/SSE 直接未实现，未证明握手/关联/超时/隔离。需要 MCP 时先评估成熟 SDK 和协议版本，而非接此草案。 | 禁止原样接线 |
| `utils/normalization_agent.py` | `resume_entity_linking.py`、人工审阅 revision、已发布词典边界 | 通用 OpenCode Agent 内核不替代本项目领域证据；候选 `_save_mappings` 将模型新映射直接写 `config/defaults/dictionaries/*.yaml` 并吞异常，越过人工确认。 | 禁止原样接线 |
| `utils/permissions.py` | `agent_runtime/permissions.py`、`tool_policy.py`、服务端 Principal | OpenCode `permission/evaluate.ts`；候选另建 saved reply/请求回调状态，但无当前用户身份与跨进程授权回放。 | 隔离 |
| `utils/plugin_system.py` | `tool_factory.py` 与固定工具/角色拓扑 | OpenCode `plugin/index.ts`；候选 `importlib` 动态加载和 hook registry 没有现行产品插件边界、部署来源或权限合同。 | 隔离 |
| `utils/resume_agent_loop.py` | L0 简历流水线、`resume_confirmation.py`、人工审阅 | 无通用上游可代替领域提取/确认；候选 Parse→Extract→Normalize→Validate→Display 另起闭环，未接当前真实文件页码/任务账本/owner 成果。 | 隔离 |
| `utils/session_queue.py` | `session_coordinator.py`、`turn_idempotency.py` | OpenCode `session/run-state.ts`；候选 `deque`/`asyncio.Task` 为进程内 steer/queue，不可替代 Gunicorn+Redis/MySQL 的跨进程状态。 | 禁止原样接线 |
| `utils/skill_normalizer.py` | `resume_entity_linking.py`、词典和人工审核边界 | 没有同构通用上游；候选 `_save_mappings` 对仓库 YAML 直接写入并吞失败，与 `normalization_agent.py` 同类风险。 | 禁止原样接线 |
| `utils/skill_system.py` | 当前项目能力配置加载和显式工具注册，非 live Skill 插件 | OpenCode `skill/discovery.ts` / `skill/index.ts`；候选读取 SKILL.md 并注册执行器，但没有项目当前所需的来源授权、发布和运行验收。 | 隔离 |
| `utils/state_management.py` | LangGraph MySQL checkpoint、Redis 会话、MySQL 任务/幂等账本 | OpenCode `storage/storage.ts` / `snapshot/index.ts`；候选 `ReplayableState._history` 为进程内列表，undo/redo 不代表跨进程恢复。 | 隔离 |
| `utils/tool_schema.py` | `tool_policy.py`、`tool_result_channels.py`、现有工具适配 | OpenCode `tool/schema.ts` / `tool/tool.ts`；候选仅浅查输入类型、`validate_output` 恒 `True`，不能取代真正 JSON Schema、权限和结构化输出验收。 | 禁止原样接线 |

## 接手条件与保护

目前最短可执行决定是**不把这 22 件放进 Q1 镜像或默认 Agent 主链**；不要从 `.opencode/MIGRATION_PLAN.md` 的标题反推实施状态。未来发现一个具体、可重放的默认路径缺陷时，先定位真实入口、Principal/状态/预算/工具结果，再按上述上游 commit/模块和许可证核对是否已有成熟实现；若采用薄适配，先写红灯测试并跑真实 HTTP→权威持久结果。若只是文件过时，须先确认原作者/其他工作使用，再以保留原文和可复核归档记录处理；本轮无删除/移动许可，也不把未知归属自动判为可清理。

**验证限度：**逐文件静态核查、现行入口与上游快照对照，以及既有替身 HTTP/本轮隔离镜像的窄链运行证据；未对这 22 件执行模型/文件/工具生产回合，不声称其全部动态不可达。现行默认 Agent 真实模型、长上下文和跨 worker 旅程仍需独立验收。
