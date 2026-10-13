# Pipeline 真实状态事件与确认事务

2026-10-09：已用当前 Docker MySQL 验证的窄切片，不是全链恢复交付。

## 复用来源与边界

- 本地 `opencode` commit `388406238bd5ca15564a762840a2362c3a45bd9c`，`packages/opencode/src/session/run-state.ts` / `status.ts`：运行状态随真实 runner 生命周期变化。该 Effect/TypeScript 进程内状态模块不提供本项目 MySQL 业务事务的可重放日志，不能直接当持久任务恢复器。
- 本地 `openai/codex` commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`：沿用既有确认切片的 approval 边界参考，模型不能替用户确认；本次没有复制 Rust runtime。
- 实际复用 SQLAlchemy **2.0.25** `Engine.begin()` / `Connection.execute()` 和 MySQL InnoDB 行锁、trigger 原子提交，不创建平行通用队列、状态机或消息总线。[事务官方说明](https://docs.sqlalchemy.org/en/20/core/connections.html#connect-and-begin-once-from-the-engine)。本地 LangGraph **1.2.9**。
- 已检查 `backend/app/services/langgraph_agent.py:get_checkpointer`：初始化失败会回退 `MemorySaver`。本轮开始时 `mkg` 运行 `import langgraph.checkpoint.mysql.pymysql` 得到 `ModuleNotFoundError`；随后补装 requirements 已声明的 `langgraph-checkpoint-mysql[pymysql]==3.0.0` 并固定该依赖版本。安装未升级现有依赖。因此不能把旧函数名解释成历史上持久恢复已启用。[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 要求 checkpointer/thread/Command resume；业务确认 API 本身并不是 interrupt。

## 数据流

```text
页面 / Agent / 历史入口 → pipeline_task INSERT / status UPDATE
  → 同一 MySQL 事务的 trigger → pipeline_task_event
  → 按 Principal + task_id + cursor 查询真实已提交状态
```

新增 `pipeline_task_event` 仅存事件序号、task_id、owner、status、时间，不复制简历、模型消息、文件路径。创建任务记录一次初态；只有 status 变化再记事件，同状态重复写不记；事务回滚事件也回滚。所有旧入口的真实状态变更都在 DB 边界覆盖，而不是靠每个路由另写一份日志。

### 部署迁移

在仓库根运行（MySQL 必须已启动）：

```powershell
$env:PYTHONPATH='backend'
$env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m app.services.pipeline_events
```

添加表及版本化 trigger，重复运行不重复创建；不回填历史、不在请求中执行 DDL。当前本地 Docker MySQL 已执行。其他部署必须显式执行；仅启动旧 Docker 不会自动装载新 API。回退应用代码可保留追加表/trigger，不删除数据。未来修改 trigger 应新增受控迁移，而非悄悄替换同名定义。

### API

`GET /api/pipeline/events/<task_id>?cursor=0&limit=100`

- 只认服务端 Principal；不存在或跨 owner 都是 404，未认证沿用 401。
- cursor 是该任务最后消费的 `event_id`；limit 为 1–200。返回 `events/cursor/has_more/task/resume_state`。
- `task` 是同一 repeatable-read 快照中的权威任务 id/pipeline/status；分页尚未读完时不能将当前页最后一项当作终态。
- 事件不生成百分比，不使用定时器推算状态。客户端可重复读取同一 cursor；不会消费或删除日志。
- **cursor 仅用于单个任务，不得汇总多个任务为全局 cursor**；不同任务事务的提交顺序可能与 AUTO_INCREMENT 分配顺序不同。
- 无迁移或 DB 故障返回 503，不制造空的成功进度。迁移前任务可能没有事件，`task.status` 仍是真实当前状态；不伪造缺失历史。

## 确认的原子性

真实 `MySQLClient` 的确认复用单个 SQLAlchemy 事务：先用学生唯一 profile 行串行化同 owner 的并发，再锁目标任务，执行既有归属/结构/缺页授权校验，写 profile 和 task，并在事务内回读。任一步失败，两张业务表和 trigger 事件一起回滚。首次 profile 占位行只存在于未提交事务；拒绝确认不会留下空档案。

同候选并发/重复确认均返回同一成果引用，只有一次 `completed` 状态事件。旧已确认候选被后来资料替代时仍拒绝；这不代表替用户选择不同候选，也没有自动确认或自动续跑 L2。

## 实测与未完成

```powershell
$env:CPA_TEST_PIPELINE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_pipeline_event_journal.py backend/tests/test_resume_golden_path.py -q
```

测试使用 UUID 隔离业务数据，finally 清理，保留部署用表/trigger。验证真实 Flask owner/cursor API、连接重建后重放、事务回滚无事件、同状态无重复、双线程确认幂等、profile 写后故障全回滚以及分页状态一致；原 gold 单测另覆盖业务入口，但解析/模型仍是替身。

本轮上述 7 个真库 journal 测试 + 13 个 gold 单测 + 11 个来源门禁单测共 **31 passed**。

### 独立 MySQL checkpoint / interrupt 试验（非生产 API）

```powershell
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -m pip install 'langgraph-checkpoint-mysql[pymysql]==3.0.0'
$env:CPA_TEST_PIPELINE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_pipeline_checkpoint_mysql_slice.py -q
```

**1 passed**：用 UUID thread 和 5 个先后独立 Python 进程依次启动 `StateGraph` → `interrupt` 持久化 → 新进程跨 owner 恢复拒绝 → 新进程确认仍待处理 → 新进程 `Command(resume=True)` → 最后新进程回读已确认/无 pending。每次仅实例化库的 `PyMySQLSaver`，没有 MemorySaver fallback；测试 finally 仅删除自己的 checkpoint rows。

运行组合：LangGraph 1.2.9、langgraph-checkpoint 4.1.1、langgraph-checkpoint-mysql 3.0.0、SQLAlchemy 2.0.25、当前 Docker MySQL。首次真实执行报 `1267 Illegal mix of collations`，随后复用现有 `_align_checkpoint_collation()` 将 checkpoint 表对齐 `utf8mb4_0900_ai_ci` 后通过；迁移已作用到当前本地库。其他部署需同样校验，不要捕获此错误后称可恢复。

此试验状态仅含 owner/candidate reference/confirmed 布尔值，owner 边界是试验 wrapper，不是平台认证路由；没有调用真实简历模型、业务确认、Supervisor 或 L2。它证明依赖和数据库支持的中断/进程间恢复 primitive 可行，**没有证明全链幂等、副作用补偿、同时 resume 的协调或真实进程 crash 后自动调度**。在这些接线完成前，生产 `TurnOutcome.resume_state` 仍为 `not_available`。

**未完成项**：L0 同步请求不能在进程重启后接续；此 journal 不调度任务，不将孤立 running 自动判成功；未接 LangGraph 持久 checkpoint/interrupt，`resume_state` 坚持 `not_available`；未接 AG-UI、前端消费或 Agent 新工具接口；未做真实简历模型全链和 worker 崩溃恢复。日志保留/归档策略也需后续配置。不要因为事件可重放而声称执行可恢复。
