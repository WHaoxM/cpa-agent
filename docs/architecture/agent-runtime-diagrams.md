# AI 工作台运行细图

这两张图是 [当前系统架构总览](current-system-overview.svg) 的钻取视图，描述工作台 Supervisor 回合的上下文流与本轮状态机；它们不是目标架构，也不把会话等同于可恢复的长期工作流。

## 阅读顺序

1. [上下文流图（SVG）](agent-context-flow.svg) · [Mermaid 源文件](agent-context-flow.mmd)：从工作台显式模式和 owner 会话校验开始，追踪用户 STM、SessionState、ModeRuntime、按模式上下文预算、PromptSelector、子 Agent TaskPacket 与工具结果回写。
2. [回合状态机（SVG）](agent-turn-state-machine.svg) · [Mermaid 源文件](agent-turn-state-machine.mmd)：对照 Ask、Agent、Experts 三条分支看澄清、权限 gate、工具执行、MySQL 结果核验、计划逐步提交和 TurnOutcome。

## 关键边界

- 上下文流图的前端入口仅表示工作台的 HTTP 模式：显式发送 `interaction_mode`，默认 Agent；Mock 模式不经过本图，也不连接后端。`mode=langgraph` 是后端 Supervisor 的执行路径。若通用 API 请求未传 `interaction_mode`，后端才按 `plan_mode > session_mode > ask` 选择。
- 回合准入中 `Idempotency-Key` 是可选请求头；Supervisor 同会话回合串行执行。Redis 租约是否强制由配置决定，开发配置可回退本进程锁。
- 会话近期历史最多取约 16 条；Ask 的 history slot 预算为 6,000 字符。Agent / Experts 的原始 history slot 为 0、模型 `messages=[]`，路由使用摘要、能力卡、记忆摘要、附件卡和当前消息（Experts 另有 EvidencePack）。这不表示子任务绝不接触当前原话：首个 TaskPacket 可含 `user_utterance`，后续计划步骤使用 lean brief。
- 子 Agent 不自动获得完整会话历史或 LTM；只获得受限 TaskPacket 和 `blackboard.read_slice_for_model` 投影。附件目标有单独的 docs pack 注入分支。每次 ScopedInvoke 创建临时 mini-graph；工具经 PreToolUse / 白名单 gate，`student_id` 由服务端绑定。
- Blackboard 只保存经过白名单过滤的上下文/结果指针；需要确认的业务成果通过 owner-bound MySQL 回读核验，MySQL 是权威业务结果，不以模型文本或 Blackboard 指针代替。
- `TurnOutcome` 的 `completion_scope=turn`，`resume_state=not_available`。`running` 只有服务端 `run_id` 存在时才成立；这不是持久 LangGraph checkpoint，也不是平台级通用任务状态机。Experts 计划逐步验证并提交；中途失败停止后续步骤，但已提交的前序步骤保留。
- `/chat/stream` 先发 `: connected`，当前实现随后完整执行并结算回合，再发送已生成的 events；不要把它解读成工具/模型步骤的实时增量事件流。

## 依据与验证边界

图依据当前接线的 `backend/app/api/agent.py`、`backend/app/services/agent_runtime/{mode_runtime,context_assembler,budget,task_packet,blackboard,scoped_invoke,plan,plan_evidence,turn_contract}.py`、`backend/app/services/session_manager.py`，以及前端 `cpa-xingtu/src/views/student/AIAssistant.vue`、`cpa-xingtu/src/api/agent.ts`。没有把未接线的 `session_runner.py` 或 `agent_loop_integration.py` 当作当前执行路径。

本轮主代理在 `mkg` 环境实际运行了 5 组测试（55 passed）和 1 次 HTTP 多轮替身旅程（1 passed），用于核对现有回合链路。替身旅程不证明真实模型行为或 Redis 跨进程租约。本次制图另行完成 Mermaid 渲染与 Chrome 目视检查；这只验证文档产物，不等同后端验收或运行时验证。真实模型、真实 Redis 跨进程协调和部署环境中的 SSE 时序仍未验证。

绘图基线见总览图及文档索引：主仓 HEAD `590f055b5307f3704afab32df754aa28e14326e1`（dirty），前端 HEAD `da7a3fa81cdf28f2ba1ab1d1396a37944e28ea0d`（dirty）。

## 持久 Agent run 计划完整性（2026-10-24）

worker-backed Agent run 将不可变计划写入 `agent_run_plan.plan_json`，并将有序步骤写入 `agent_run_step`。owner 读取时，`_plan_view` 校验计划摘要、规范化步骤，并逐项核对关系表中的目标、顺序、角色、步骤目标和 Skill ID。若内容不一致或缺少步骤，读取以 `AgentRunStoreError` fail closed；执行器不能运行与展示计划不同的步骤表。这与 `docs/guides/agent-plan-persistence.md` 中保存到学习中心的行动计划/artifact 是两条不同流程。

离线回归 `backend/tests/test_agent_run_store.py::test_plan_projection_rejects_step_rows_that_diverge_from_immutable_plan` 使用内存 SQLite fixture，不代表已验证真实 MySQL 部署。
