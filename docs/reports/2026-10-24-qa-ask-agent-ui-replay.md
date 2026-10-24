# 2026-10-24 Ask / Agent 隔离 QA 页面回放

## 范围与环境

本记录覆盖本机隔离 QA 页面、QA 后端和 worker。只使用合成测试账号与合成课程任务。当前已核验的页面为 `http://127.0.0.1:5176/`，QA 后端为 loopback `:5003`；生产后端 `:5001`、原 QA 前端 `:5175` 与既有 QA 后端 `:5002` 未切换或重启。Qwen 通过 loopback 预算代理，整个 QA 过程总计 **4/10** 次实际 provider 请求；本次 Ask 回放新增 2 次。没有进行联网搜索，Tavily 未配置。

## Ask 事实边界与会话恢复

合成用户询问数据库隔离级别课程如何安排学习。首次真实 Qwen 回答出现“≤45分钟”的具体阈值；复核本轮输入、可见历史、被选择的 Skill 和源码后，没有找到该阈值的依据。基座提示随后补充：区分事实、一般知识和建议；不把无来源数字、时长、统计或来源说成事实；不把建议扩大成“任何/所有/必然/一定”适用。修改位于 `backend/config/prompts/agent.ask.system/base.md`，选择器回归在 `backend/tests/test_prompt_selector_matrix.py`。

用户对阈值作出纠正后，第二次真实 Qwen 回答把方案限定为一般尝试方法，说明适用性取决于课程内容、目标和可用时间，没有给出固定时间或频次，也未声称联网检索或读取个人资料。关闭并重新打开同一浏览器会话 `session_e2d5d27f8f62` 后，原回答、用户纠正和修订回答均在页面历史恢复。该场景的回答修订与历史恢复通过；没有据此宣称组件快照已持久化。

本轮执行的命令：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_prompt_selector_matrix.py -q
```

结果：`8 passed`。真实 Qwen 请求次数不由该测试产生；QA 预算代理总计为 4/10。

## Agent 派发失败诊断

合成任务 `run_99073b5ac09d4d9bb21bef4d9f80e51b` 的持久记录显示 `queued` 与 `dispatch_claimed`，没有计划版本或执行结果。QA worker 在线，注册了 `cpa.agent.run` 与 `cpa.agent.replay`，监听对应 QA 队列；检查时 active/reserved 均为 0。只读 QA Redis 检查得到队列长度 0、未确认消息 0。按 run ID 筛选的应用日志只有 middleware 请求记录，没有 worker traceback，因此原始那次 worker 异常日志未保留。

通过相同 QA runtime 配置单独重现 executor 前置步骤，`AgentRunExecutor.execute_claim()` 在构图与调用任何模型前进入 `_durable_checkpointer()`，后者调用 `PyMySQLSaver.setup()`。已安装 `langgraph-checkpoint-mysql==3.0.0`，共有 22 个迁移；QA `checkpoint_migrations` 当前只有 0–3，因此 v4–v21 全部待执行。v4 要求 `ALTER TABLE checkpoint_blobs MODIFY COLUMN blob LONGBLOB`；v5–v8 创建索引，v9–v11 扩大 namespace 列，v12–v17 重排主键并引入 namespace hash 列，v18 增加 `task_path`，v19–v21 再调整 hash 列。上游 `setup()` 对每条迁移单独 `COMMIT`，所以完整过程可能留下部分已应用的版本，不能只盯住首个 v4 错误。当前 QA runtime 账号没有 `ALTER` 或有效全局权限；以该账号运行 setup 在 v4 得到 MySQL 错误 1142。该失败与任务停在 claim 后、无计划、无模型调用的状态相符。当前三张 checkpoint 数据表行数均为 0，迁移表有 4 个版本行。

在执行任何 DDL 前，已将四张 checkpoint 表的结构与当前迁移版本导出到私有 QA 目录文件 `checkpoint-schema-before-upstream-setup-20261024T085121Z.sql`（1889 bytes，SHA-256 `765e22b872c792dfc013c61de8ba12feb929ff97e19c9a9307767bf3103937b4`）；ACL 核验为当前 Windows 用户独占 FullControl。该快照只含 checkpoint schema 和迁移版本，不含项目业务资料。项目内现有 QA launcher 与 MySQL helper 均使用 runtime 配置，没有独立 migration 身份或专用入口；本轮没有找到可用的既有 QA-only 管理迁移凭据。因此未运行官方 setup，也未执行手工 SQL、创建新迁移身份或扩大 runtime 权限。取消的 run 未重放。

随后通过真实 QA 页面取消该 run；事件序列记录提交、claim、取消请求和安全点取消，最终 run 与 dispatch 状态均为 `cancelled`，`cancel_requested=true`，没有任何 step claim 或计划。取消后的 run 不再被派发。Qwen 计数未因该 Agent run 增加。

**剩余的精确阻塞**：checkpoint v4–v21 迁移需要独立的 schema migration 身份或已经批准的 QA-only DDL 执行入口；现有 runtime 账号有意不具备 `ALTER`。不能把 QA schema 标为迁移完成或 Agent 派发通过。继续前需提供既有受限 migration 身份/入口，或另行批准创建仅限此 QA schema 的一次性 migration 身份。执行后需验证官方版本表到 21、列/索引/主键形态与 worker runtime 使用成功；生产库未接触。

本轮没有修改 Agent executor/worker 路径：当前阻塞是缺少独立 migration 身份，而不是安全地扩大 runtime 权限即可解决的代码参数；且该路径仍由 Agent 运行时改造 owner 维护。原始 worker 异常 traceback 没有保存，因此这次结果是“同一 executor 前置步骤的受控复现”，不是声称已取得原失败堆栈。

## 迁移后状态更新（2026-10-24）

本节覆盖前文“尚缺 migration 身份/入口、未执行官方 setup”的当时状态；现以此节为准。用户授权的现有本地管理路由已被复用，但连接显式限定到 `cpa_agent_qa_20261024_01`。没有读取进程环境，没有把生产 schema 作为迁移目标，也没有增加 QA runtime 权限。

迁移入口 `backend/scripts/ops/migrate_qa_checkpoint_schema_once.py` 固定 loopback MySQL `127.0.0.1:13306` 和目标 QA schema；先验证根 `.env` 的默认 schema 是 `cpa_agent`、预迁移备份 SHA-256、预期初始版本 0–3 及 checkpoint 数据表为空，再调用已安装的官方 `PyMySQLSaver.setup()`。程序随后验证版本 0–21、列/索引/主键结构、数据表仍为空，并用 runtime 账号执行 setup smoke test。最终脱敏结果：`migration_versions=0-21`、`final_schema_verified=true`、`runtime_setup_smoke=true`、`runtime_alter_granted=false`；目标仅为 `cpa_agent_qa_20261024_01`。备份仍在 `%LOCALAPPDATA%\CPA-Agent\qa\checkpoint-schema-before-upstream-setup-20261024T085121Z.sql`，1889 bytes，SHA-256 `765e22b872c792dfc013c61de8ba12feb929ff97e19c9a9307767bf3103937b4`，ACL 仅当前 Windows 用户 FullControl。

入口采用上游 `langgraph-checkpoint-mysql==3.0.0` 的 22 项官方迁移定义与 `PyMySQLSaver.setup()` 顺序；没有自写通用迁移器、跳过版本或授予 runtime ALTER。执行前只检查目标库的空 checkpoint 状态，执行后检查官方版本表和实际 schema。该步骤只验证 QA 中 checkpoint 初始化；未对生产运行数据库执行迁移，也没有声称原取消 run 已恢复。

新增防护测试：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_qa_checkpoint_migration_guard.py -q
```

结果：**6 passed**。测试覆盖固定 QA 主机/端口/schema、拒绝生产 schema 和配置切换、备份哈希/上游版本保护；它不是整套 migration 模拟或生产 migration 验证。调用官方 setup 的实际 QA 结果如上。

之后尝试恢复真实 QA UI：Edge 标签清单中可见 `http://127.0.0.1:5176/app/dashboard`，但 CUA 绑定失败；按标签 ID 返回 `Debugger unattached`，按 URL 返回 `Tab not found in browser 1`。两次均未产生页面操作，未创建新 Agent run，Qwen 共享计数仍是最后核验的 **4/10**，没有新增调用。故迁移后 Agent 成功计划及外部组件刷新/重进回放仍未测，不能把 schema smoke test 说成 Agent 端到端通过。当前无可导出的本地截图或 Library ID。

## Agent UI 补充回放（2026-10-24）

重新发现浏览器后新建 QA 标签 `http://127.0.0.1:5176/` 并进入 AI 助手。没有访问其他标签。新合成 run `run_fcb40163d6a747659e23fd3877b01ad5` 的输入为：“为虚构学生制定三步数据库隔离级别学习计划，列出目标、行动和完成标准；不联网，不读取资料，不执行操作。”页面把请求转入 Agent，生成第 1 版计划；计划内容却是“依据本轮真实资料陪学生比较、澄清和复盘”，步骤“读取绑定的探索资料并形成待确认建议”显示已完成，当前结果则反问“这是为你自己学，还是为帮其他同学设计？”。因此这条 Agent 流程没有满足目标，也与明确的“不读取资料”约束冲突。页面没显示任何读取到的个人资料内容，不能据此断言实际读出了资料；但执行状态把读取步骤标成完成，必须作为授权边界/执行顺序缺陷处理。本轮没有点击保存计划。

刷新该会话后，任务状态、目标、计划第 1 版、步骤和当前结果均再次显示；这证明该 Agent 计划卡片可从服务端状态恢复，但只通过了快照恢复这一部分，内容质量仍失败，也不等同于用户确认保存后的 artifact。

对完成 run 的一次合成澄清跟进，页面先显示“等待当前任务到达安全点”，并提示必须复用原请求编号。没有盲目重发；点击“重新读取”后服务端明确返回“任务版本或当前任务状态已变化，这条跟进没有应用”。所以 follow-up 未应用，不能算通过。QA Qwen 预算代理从本轮开始时已知的 4 增至最后读回的 **6/10**；本轮 Agent run 与一次 follow-up 尝试共消耗两个预算槽位。此后没有新的 provider 请求。旧取消任务没有被重放。

随后打开一个新任务并点选“学习计划”快捷入口，但输入同步出现问题，发送按钮保持禁用；没有发送第二个 Agent 请求。浏览器标签已通过受支持的 CUA 路径恢复并标记交接。页面截图只以内嵌浏览器工具结果显示，工具没有提供本地导出路径或 Library ID。

本轮结论：真实 UI 的 Agent run/worker/计划卡片读取路径可达，刷新后卡片快照可恢复；计划内容/读取约束及完成态跟进存在失败，未保存用户确认计划。剩余 4 个预算槽位未使用。不得据此声称 Agent 端到端验收通过。

## 测试矩阵

| 场景 | 结果 | 证据与边界 |
|---|---|---|
| Ask 回答事实边界提示回归 | 通过 | `test_prompt_selector_matrix.py`，8 passed；这是选择器/提示回归，不是普遍事实性评测。 |
| 真实 Ask 首答与纠正 | 部分通过 | 两次真实 Qwen 请求；首答出现无来源时间阈值，纠正后改为条件化方法。 |
| Ask 历史重新打开 | 通过 | 同一 session 的原文、纠正与修订回答在重新打开后恢复。 |
| Agent 合成任务派发 | 失败 | 迁移完成后真实 UI 新建 run 并由 worker 完成，但计划要求读取绑定探索资料且结果转为澄清问题，与“不读资料、生成学习计划”目标不符。 |
| 取消安全点与持久状态 | 通过 | 真实页面取消；run/dispatch 都是 `cancelled`，无步骤执行；未重放。 |
| Agent 计划卡片刷新恢复 | 部分通过 | 同一 run 的已持久化计划第 1 版与结果刷新后重新显示；内容不合格，且没有确认保存为用户 artifact。 |
| Tavily / 联网搜索 | 未测 | provider 未配置。 |
| Agent 跟进/修订 | 失败 | 一条合成澄清消耗预算后以任务版本/状态变化被拒绝，页面明确显示跟进未应用；未重发。 |
| Agent 暂停/续跑/幂等 | 未测 | 本轮未覆盖；取消的旧 run 未复用。 |

## 截图与运行状态

本次真实浏览器截图以工具内嵌图像形式显示；没有可用的本地导出文件或 Library ID。重新打开后的会话恢复由页面可访问树文本核验，页面截图当时滚动位置仍显示早先回答，不能当作修订后答案的截图。QA backend、worker、Qwen 预算代理和 5176 QA 页面保持运行；本报告没有停止服务。没有改生产数据库、生产后端、SSH/隧道、Git main 或部署。

## no-read 边界补测（2026-10-24）

针对上节 Agent run 让“读取绑定资料”步骤显示完成、但任务又明确禁止读取的缺陷，服务端现在对 no-read run 使用固定的一步通用问答计划，并在规划、执行 claim 和步骤开始前校验该计划；不符合固定安全目标或试图带入 owner-bound 读取 Skill 的计划会 fail closed。此分支不调用职业资料读取或教育筛查个人资料流水线；允许保留的 Skill 仅限服务端审核的分析方法，不把 Skill 当作证据。对有歧义的禁止范围先进入澄清状态。该边界有执行器实际调用测试，不仅依赖提示词。

本次修改：`backend/app/services/agent_runtime/resource_access_policy.py`、`backend/app/services/agent_runtime/agent_run_executor.py` 与对应的 `test_agent_run_resource_access_policy.py`、`test_agent_run_executor.py`、`test_agent_run_store.py`。存储回归还验证：v1 的读取回执不能证明修订后的 v2 步骤已读取。定向命令及结果：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_run_resource_access_policy.py backend/tests/test_agent_run_executor.py backend/tests/test_agent_run_store.py -q
```

结果：`59 passed in 8.80s`。覆盖 no-read 固定计划、禁止个人资料读的多类能力执行路径、允许读取的对照路径、计划篡改拒绝，以及旧版本读取回执不能跨计划版本完成新步骤。该命令使用项目 `mkg` 环境的测试替身，不是 59 次真实请求。

随后在 `http://127.0.0.1:5176/app/student/ai-assistant` 的隔离 QA 页面提交一个合成通用问答任务，run 为 `run_83576da9883b4f13aae6af9925ac97bb`，session 为 `session_6beaed3e7336`。输入要求解释读未提交、读已提交、可重复读和串行化，给出复习顺序及自测练习，并明确不联网、不读取任何资料/个人档案/历史、不执行保存或投递。真实 QA worker 将其完成为一个步骤；结果给出四种隔离级别的对照、复习顺序和自测练习。服务端回读显示 run/步骤完成、dispatch claim_attempts=1，步骤 `tool_calls=0`、无 tool names、无 Skill、无已核验外部事实；这支持“该 run 未调用个人资料读取工具”的结论，不证明一般知识内容逐条经过来源核实。答案中有关数据库产品默认隔离级别的说法未做来源核验。

刷新页面后，同一用户输入、结果和完成计划仍从服务端恢复。没有点击“保存计划”，所以这只验证运行结果/计划显示恢复，不验证保存为用户 artifact；该通用问答也没有组件快照，因此不覆盖组件持久化恢复。QA Qwen 共享预算代理从之前核实的 `6/10` 增至 `7/10`，本 run 新增一个实际 provider 请求；没有重放旧取消任务，也没有把计数重置。QA backend、worker、预算代理及 5176 前端继续运行；生产 backend、生产数据库、隧道与其它前端进程未改动。

这一补测修复并覆盖了新 run 中的 no-read 边界，但不会抹去前一 run `run_fcb40163d6a747659e23fd3877b01ad5` 的历史失败证据，也不代表通用 Agent 端到端验收全部通过。旧失败 run 保持原样，没有改写其历史或伪报成功。实际页面内容在浏览器会话中核验；本轮没有取得可验证的本地截图文件或 Library ID，因此不把工具截图当作可交付图片。

| 新补测项 | 结果 | 证据与边界 |
|---|---|---|
| no-read 计划与执行隔离 | 通过 | 定向测试 `59 passed`；真实 QA run 的工具调用数为 0，步骤/dispatch/终态由 QA MySQL 回读。 |
| no-read 合成 Agent UI 回答及刷新恢复 | 通过（范围有限） | `run_83576da9883b4f13aae6af9925ac97bb`，真实 QA 页面与 worker；刷新后恢复。一般知识中的产品默认值未做来源核验。 |
| 前一条不合规 run 的历史 | 失败（保留） | `run_fcb40163d6a747659e23fd3877b01ad5` 的计划/结果未修改；新修复不回写旧事件。 |
| 工具读取/Skill 正向对照 | 单测通过 | 执行器对照测试验证允许读取时仍走绑定资料路径；本次真实 UI 输入明确禁止读取，没有实际调用读取工具。 |
| 外部组件快照保存及重进 | 未测 | 本轮通用 run 没有组件数据，也没有保存 artifact。 |
| 暂停、恢复、修订、幂等 follow-up | 未测/仍有既有失败 | 前一轮 follow-up 被状态/版本冲突拒绝；本轮未重发或新增请求。 |
| 联网搜索与证据引用 | 未测 | Tavily 未配置；本 run 明确禁止联网。 |
| 截图文件导出 | 未取得 | CUA 结果曾以内嵌图显示，但无可验证本地导出路径或 Library ID。 |

## 保存 artifact 与 follow-up 最终验收（2026-10-24）

本节覆盖上方矩阵中“外部组件快照保存及重进未测”的旧状态；它验证的是 synthetic QA 中明确确认的保存计划 artifact，不代表所有组件类型都已验收。

### 真实 QA 页面路径

- 页面：`http://127.0.0.1:5176/app/student/ai-assistant?session_id=session_95c3d9374a5f`。
- 用户输入：“为虚构学生制定一份数据库隔离级别学习计划，答案分三步呈现，每步写清目标、行动和可验证完成标准。只用一般知识，不联网、不读取个人资料、绑定材料或历史记录。先展示计划；本轮不要自动保存，我会在确认版本后再做一次明确的保存验收。”
- 关键操作：等待真实 QA worker 完成 run `run_b7f822eda7f146ff9958ce3f4a918242`；核对页面计划 v1；用户明确确认后点击“确认保存计划第1版”；点击“读取此版本”；刷新页面后再次读取 v1。
- 预期：先展示计划而不自动保存；显式确认绑定当前计划版本；刷新/重新读取恢复原快照，不让模型重新生成已保存内容。
- 实际：run 生成三步计划，no-read 固定计划的 UI 状态为 v1。未确认前没有保存；确认后页面显示“已确认保存的版本”、计划第1版/契约第1版。读取及刷新后的回读摘要 digest 均为 `8606b317f86d9e8964693bf2de7e24c67f3066a66f3be9018966dc2a51c1d892`，请求内容和计划正文一致。**此合成 QA 场景通过**：保存确认、版本绑定、owner session 的回读和刷新恢复均由实际页面及持久服务端结果核验。此证据不能推出任意用户/任意 artifact 类型都通过。

### follow-up 失败保留

同一 run 随后输入：“请在现有计划中补充一道判断脏读与不可重复读的自测题，保留三步结构和可验证标准；继续只用一般知识，不联网、不读取任何资料。不要保存新版本，先让我核对修订结果。”页面首次反馈任务尚待到达安全点并要求复用同一请求编号。没有盲目重发。刷新后系统进入“你是要补充当前任务、另开新任务，还是查询进度？”澄清，计划仍为 v1，保存 artifact 仍为 v1，故该 follow-up **未应用/失败**，不能算修订成功。该尝试后共享 QA Qwen 计数从 `8/10` 变为 `9/10`；总数 **9/10**，没有重置或继续请求，保留 1 个预算槽位。没有把旧取消 run 重新派发。

### 本轮确定性回归

命令：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_run_task_contract_integration.py backend/tests/test_agent_runs_api.py backend/tests/test_agent_run_store.py backend/tests/test_action_plan_persistence.py -q
```

结果：**68 passed in 11.67s**。覆盖 synthetic API/store 下的计划版本保存确认、owner 读取、保存幂等/不确定结果回查、自然语言 follow-up 分类与 revision、重复请求对账、stale revision/非当前任务冲突、pause/resume 控制状态及 artifact 持久化读回。这里使用的是测试数据库/替身与确定性请求，不是实时 UI 的暂停/恢复或模型调用证明。

本轮此前 no-read 修复的相邻定向命令仍为：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_run_resource_access_policy.py backend/tests/test_agent_run_executor.py backend/tests/test_agent_run_store.py -q
```

结果：**59 passed in 8.80s**。此外 `test_prompt_selector_matrix.py` 为 **8 passed**。这些测试均不增加真实 provider 计数。没有运行整个后端测试套件。

### 最终矩阵与发布门槛

| 场景 | 最终状态 | 可核验证据 / 未覆盖边界 |
|---|---|---|
| Ask 历史纠正后重新打开 | 通过（范围有限） | `session_e2d5d27f8f62` 原文、纠正与修订答复恢复；不代表答案都完成来源核验。 |
| Ask no-read / Agent 通用问答 | 通过（范围有限） | `run_83576da9883b4f13aae6af9925ac97bb`；真实 QA worker，工具调用 0，刷新恢复。一般知识内容未逐句联网核实。 |
| Agent 明确禁止资料读取的服务端边界 | 单测及一个真实 QA run 通过 | no-read 专用政策覆盖多条 specialized pipeline；执行前 fail closed；旧版本读取回执不能完成新版本步骤。未测试所有真实数据来源连接器。 |
| 确认保存计划 artifact、版本回读与刷新恢复 | 通过（一个 synthetic QA 计划） | `run_b7f822eda7f146ff9958ce3f4a918242`；保存 v1 后刷新、读取仍同 digest。其他组件种类与生产账号未测。 |
| 同一 run 的 follow-up 修订 | 失败 | `run_b7f822eda7f146ff9958ce3f4a918242` 的跟进没有应用，刷新后变成对象澄清，计划/artifact 未变化。不得重试已消耗的请求。 |
| Agent 暂停/续跑/幂等控制 | 确定性测试通过，真实 UI 未测 | 上述 68 项测试覆盖 API/store 状态和幂等；未在浏览器执行暂停/恢复，也未声称完整 UI 闭环。 |
| 已取消 run 防重放 | 通过 | 先前 QA worker 核验两条重复队列消息不执行，持久状态 `cancelled`、`cancel_requested=true`、`claim_attempts=0`；本轮未重放。 |
| Qwen 预算上限 | 通过（当前 QA 计数） | 共享 loopback 代理计数 9/10；未重置。尚余一个授权槽位，不代表额外调用已发生。 |
| Tavily 联网搜索与真实来源引用 | 未测 | 无配置；不以模拟 provider 或一般知识答案冒充搜索验收。 |
| 外部组件通用类型、旧 schema 降级与全部刷新恢复 | 部分通过 | 只验证保存计划这一种 artifact 的已确认 v1 快照。 |
| 可交付截图文件 | 未取得 | CUA 截图以 inline image 提供且没有本地导出路径；本轮尝试输出完整 saved-artifact 页面截图时超过工具上下文限制。没有伪造截图路径或 Library ID。 |
| 生产发布 | 未执行/未通过 | Git 工作区保持未提交；candidate 未合并 main，未部署。生产迁移从 QA 0021 到目标 schema 的差异及生产验证仍是发布门槛。 |

截图能力检查结论：CUA `getScreenshot()` 提供图像内容用于内嵌显示，没有受支持的本地文件保存路径。本轮已有一张实际 QA 页面 inline 截图（空白新任务页，不是已保存 artifact 场景）；最终已保存 artifact 页面截图输出因内容超限被截断，未产生可交付图片。故不能以该 inline 图替代用户要求的场景截图回放。Library 报告上传结果以任务最终回报为准；截图没有上传。

QA 服务在本轮检查结束时由先前记录为：QA backend `127.0.0.1:5003`、Vite `127.0.0.1:5176`、独立 worker 与共享预算代理；旧 QA `:5002`/`:5175`、生产 backend `:5001`、数据库和隧道均未切换。本轮没有停止服务、改生产库/后端/隧道，未提交或修改 Git branch/index，也未合并 main 或部署。上述服务最后状态没有在本报告写入后重新探测，因此“仍运行”不作为本次最终 health 断言。

## Follow-up 分类根因与最终回归（2026-10-24）

为区分真正歧义与 run/revision 状态错误，对合成 QA schema `cpa_agent_qa_20261024_01` 中 run `run_b7f822eda7f146ff9958ce3f4a918242` 的单条 follow-up 记录做了限定字段只读核验，没有新模型调用。记录要点：

- 用户原文明确说“在现有计划中补充一道判断脏读与不可重复读的自测题”，保留三步结构，且“不要保存新版本”；
- request 的 `task_id` 与 URL/run ID 一致，request revision **1** 与存储的 contract revision **1** 一致；
- 操作状态为 `completed`，分类器实际消耗 **1** 次模型请求；
- 持久分类 `intent=clarify`、confidence `0.98`、结果 `ask_clarification`，问题是泛化的“补充当前任务、另开新任务还是查询进度”；但同一分类的 `reason` 明确复述了“补充当前计划”，并写明其“符合 supplement 分类定义”。

因此这次不是 selected-run 丢失、stale revision 或幂等键冲突，也没有证据显示用户原话本身需要该泛化澄清；它是模型返回的分类字段与理由相互矛盾。数据库只保留经过解析的分类包，未保留原始 JSON 的 `ambiguous` 标志/完整原始输出，所以不能进一步断言矛盾是模型的 `intent` 还是 `ambiguous` 字段造成。当前任务契约允许通过 `acceptance_criteria`/deliverables 表达明确补充；本轮未擅自把模型的 `clarify` 改写为 supplement，也未重发请求。页面按指定会话重新打开时仍选中该 run，计划 v1 与保存 artifact v1 可见；新开标签不会共享旧标签的 `sessionStorage` 跟进展示记录，因此本次新标签不显示原跟进气泡，这不等同同标签刷新丢失 selected run。

本轮最终前端全量回归：

```powershell
$files=Get-ChildItem -File tools -Filter 'test-*.mjs' | Where-Object { $_.Name -ne 'test-resume-trace-attempt.mjs' } | ForEach-Object { 'tools/' + $_.Name }
& node --test $files
node tools/test-resume-trace-attempt.mjs
npm run build
```

第一条实际用 PowerShell 枚举 `tools/test-*.mjs` 后调用 `node --test`，排除单独运行的 `test-resume-trace-attempt.mjs`；结果 **213 passed, 0 failed, 0 skipped**。trace 脚本另通过 **28 assertions**。`npm run build` 包含 `vue-tsc --build` 与 `vite build`，退出码 0；Vite 有既有 `lottie-web` `eval` 提示和大于 500 kB chunk 警告，不是构建失败。构建产物在被 `.gitignore` 忽略的 `cpa-xingtu/dist/`，没有纳入工作树候选。

最终相关后端回归命令：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_task_followup.py backend/tests/test_agent_run_task_contract_integration.py backend/tests/test_agent_runs_api.py backend/tests/test_agent_run_store.py backend/tests/test_action_plan_persistence.py -q
```

结果：**74 passed in 11.80s**。它覆盖 classifier schema/parser、follow-up 幂等/版本冲突、API/store 状态、保存 artifact 与 pause/resume 的确定性状态流；不证明实际模型会稳定把本例归为 supplement，也不等于 live UI pause/resume 已通过。预算代理仍为 **9/10**，本轮没有新的 provider 请求。

### 收尾矩阵更新

| 核对项 | 最终结果 | 说明 |
|---|---|---|
| selected run / session 上下文 | 正确 | 浏览器加载 `session_95c3d9374a5f` 后选中 run `run_b7f822eda7f146ff9958ce3f4a918242`；run 卡片、目标、计划和 saved artifact 均显示。 |
| follow-up route 与 revision | 正确绑定 | 持久 request `task_id=run_id`、expected/current revision 均为 1；操作已完成，无 stale-revision 结果。 |
| follow-up 分类 | 失败 | 高置信 `clarify` 与理由中的明确 `supplement` 冲突，产生不必要的泛化澄清；原始模型 JSON 未保存，现有 9/10 预算不重发。 |
| 前端全部 Node 测试 | 通过 | 213 passed，0 failed/skipped。 |
| 前端 trace 独立脚本 | 通过 | 28 assertions。 |
| 前端类型检查及生产构建 | 通过（有警告） | `npm run build` 成功；`lottie-web` 使用 `eval` 提示与大 chunk 警告保留。 |
| Agent follow-up/API/store 回归 | 通过（确定性） | 74 passed；不能替代真实分类质量或真实 UI 暂停/恢复。 |
| provider 用量 | 未超授权上限 | 9/10；保留最后一个调用，不再使用。 |

本轮未改前端源代码、未改历史 run 或 QA 数据；本轮没有生成新截图、重试模型、清理/停止服务、执行生产迁移、提交、合并或部署。遗留用户决策仅为是否在后续协调验收中使用保留的最后一次 Qwen 调用，对同一明确 follow-up 分类缺陷做定向复测；在获得该复测前，不应将 Agent follow-up 修订验收标为通过。

## 续接故障修复与真实 QA 第三版回放（2026-10-24）

### 根因与修改

前一条 QA 续接先出现两类可复现问题：已持久化但客户端幂等键丢失的 no-call 操作在刷新后无法安全完成；另外，API 在预算预留时把可确定分类的明确补充仍标成需模型调用，后续分类器未调用模型，空账本却被当成未知执行结果，落入 `needs_reconciliation`。

修复在确定性分类前完成预算判断；只有受限的当前任务/当前计划补充、明确目标修订和短控制指令走无模型路径，混合/含糊意图仍走真实分类器。增加 owner/current-run/current-revision 绑定的 `POST /api/agent/runs/<id>/followup/recover`：不接受新消息、不暴露摘要、不重新调用分类器；只将仍匹配当前契约、零调用且无持久结果的 deterministic no-call 操作安全对账并沿原 CAS/幂等路径应用。save 与 create-new-task 不在恢复白名单内，旧修订仍失败关闭。相关回归覆盖陈旧 revision、重复 body/key、预算账本、恢复 CAS 和 worker 对新 revision 使用独立 checkpoint。

### 本次真实 UI 与权威状态

- QA 页面：`http://127.0.0.1:5176/app/student/ai-assistant?session_id=session_fe0d92fb01ac`；合成 run `run_92299a6f689c4ad1bdb9ef9724b49fd2`。
- 用户原要求是为虚构学生生成恰好三步的隔离级别学习计划，并补齐脏读、不可重复读的具体四步时序、答案要点和核验标准；全程不联网、不读取个人/绑定资料、不保存 artifact。
- 修复后 follow-up 完成，run/contract/plan 为 v3，dispatch 为 terminal，队列为空。页面刷新后恢复完整最终答复，包含两种异常各自的 T1/T2 四步序列、定义判断、答案要点、可核验标准，以及“数据一致性保障强度”和“并发执行效率”的取舍句。答复注明一般知识、未联网、未外部核验，没有再声称查阅 SQL:2016 或“已核验证据”。
- 页面进度的 `1/1` 是内部 QA 执行步骤；用户要求的三步学习内容显示在完成答复中。该 run 未保存 artifact（无保存确认），也没有资源读取事件；原已保存计划 v1 属于之前独立 QA run，本轮没有覆盖或更改它。
- 早期失败的历史记录仍保留：第一条模型分类曾以高置信 `clarify` 与自身 reason 中的 supplement 判断冲突；另一次中间计划遗漏详细自测要求。它们没有被改写为成功，修复后由独立 synthetic run v3 验证。
- 真实 QA 共享 Qwen 计数实读为 **17/20**，本轮未重置；此次 deterministic recovery/classification 不消耗 provider 请求，计划生成的真实请求计入总数。没有执行 Tavily 搜索或其它外部资料读取。

本轮复核的命令和结果：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_run_task_contract_integration.py backend/tests/test_agent_runs_api.py backend/tests/test_agent_run_store.py backend/tests/test_agent_task_followup.py backend/tests/test_agent_run_executor.py backend/tests/test_agent_run_resource_access_policy.py backend/tests/test_agent_call_budget_ledger.py backend/tests/test_prompt_selector_matrix.py -q
```

结果 **140 passed, 2 warnings**。前端在 `cpa-xingtu` 下运行全部 `tools/test-*.mjs`（单独排除 trace 脚本）为 **220 passed, 0 failed**；`node tools/test-resume-trace-attempt.mjs` 为 **28 assertions passed**；`npm run build` 成功，包含 TypeScript 类型检查与 Vite 生产构建。保留既有 `lottie-web` `eval` 提示和大于 500 kB 的 bundle 警告。

UI 交接者另外完成 Agent 页面桌面与 433 px 窄屏的实际只读回放：无横向溢出，展开/折叠持久事件时间线，区分当前计划与已保存 v1，刷新后版本不串；相关前端测试 **67 passed**，type-check/build 通过。该只读回放没有执行新的用户任务。当前整合测试再次确认生产 backend `127.0.0.1:5001`、QA backend `127.0.0.1:5003`、QA frontend `127.0.0.1:5176` 均返回 HTTP 200，三端口各有一个 listener。

### 最终门槛与未覆盖范围

| 项目 | 状态 | 证据或限制 |
|---|---|---|
| 明确补充 follow-up 与 owner-bound 恢复 | 通过（一个合成真实 UI run） | run v3 完成；刷新恢复；无重复分类调用；零资源读取；17/20 总预算。 |
| no-read 资料边界 | 本场景通过 | 当前 QA run 的资源读取事件数为 0；此结果不能外推到所有来源连接器。 |
| 计划 v1 artifact | 保留在独立历史 run | 本轮当前 run 不保存；历史 v1 没有被覆盖。所有 artifact 类型的 schema 升级恢复仍未逐类验收。 |
| Agent 页面 P0 | 通过（有限） | 桌面与 433 px 窄屏只读检查、时间线和版本显示通过；取消显示另有 deterministic tests。真实 UI pause/resume、安全点并发及每类事件流仍未全测。 |
| Tavily / 联网引用与来源核验 | 未测 | QA 没有已配置的搜索凭据；没有真实网络搜索，不能把通用知识回答说成来源验收通过。 |
| 可下载截图回放 | 未交付 | 浏览器截图能力只提供内嵌图像，没有受支持的本地文件导出结果；Library 中本报告不含截图。 |
| 生产数据库迁移 | 未执行 | 生产 MySQL 仍需从 Alembic `0020` 到 `0021_agent_run_task_contract_control`；LangGraph MySQL checkpointer QA-only 版本 0–21 setup 也必须在生产受控迁移身份下按官方迁移程序准备并核验。runtime 权限不应因此获得 DDL。未连接生产库执行迁移。 |
| Git candidate | 本地已提交，远端候选基线已读，推送被审批拒绝 | 后端本地 `c885073`，前端本地 `9a6dd66`。正式主机确认 VPN proxy 可达；只读 `ls-remote` 读到远端分别为 `24a6eaa`、`3be50d1`。后端普通 fast-forward push 被自动审批拒绝，前端 push 未尝试；两个远端分支仍未更新。 |
| 生产 main/发布 | 未执行 | 未合并 main、未部署 frontend，也未写生产数据库。 |

本地截图文件仍未生成；截图回放要求因此没有满足。报告保留事实结果，不把 mock 或单测替代真实截图、联网或生产验收。

### 最终整合检查（2026-10-24）

后续核对发现，确定性补充虽在分类器内无需模型，但 HTTP 入口仍先按“需要分类模型”预留额度；零调用账本因此把有效补充误判为未知结果并进入 `needs_reconciliation`。现将同一窄范围纯函数用于 API 预留前分类，并将已验证分类与 `model_required=false` 一起持久化；含糊/混合意图仍需模型。HTTP 回归验证确定性补充和精确目标修订不预留模型调用、同请求只增一次 revision、重放幂等、相同 key 的不同 body 返回冲突。对应测试选择命令为：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_task_followup.py backend/tests/test_agent_run_task_contract_integration.py backend/tests/test_agent_runs_api.py -q -k "deterministic or recovery or idempotency or stale or ledger"
```

结果 **7 passed, 19 deselected**。随后最终组合后端命令（见上一节）为 **140 passed, 2 warnings**。前端最终全套 `node --test` 为 **220 passed, 0 failed**；独立 `node tools/test-resume-trace-attempt.mjs` 为 **28 assertions passed**；`npm run build`（含 Vue/TypeScript 检查）成功。另一次 P0 变更范围的独立回归为 **67 passed**；这些套件有重叠，不能相加称为互不重复用例数。构建保留既有 `lottie-web` `eval` 提示与大 chunk 警告。

QA 计数的权威状态仍为共享 Qwen **17/20**，没有重置。真实合成 run `run_92299a6f689c4ad1bdb9ef9724b49fd2` 的 contract/plan v3 已完成且刷新恢复；当前场景资源读取为 0、未保存本 run 的 artifact。早期分类冲突、中间计划约束遗漏和一次零调用 `needs_reconciliation` 均保留为失败历史，不改写为成功；最终回放通过不代表这些失败从未发生。

候选 Git 远端只读核验严格经既定 VPN 代理 `127.0.0.1:7897`；`git ls-remote` 因该代理端口连接被拒而未取得远端 SHA。没有切换直连，也没有合并 main 或部署。后端候选分支 `codex/release-candidate-20261023` 的本地提交为 `c885073`，前端同名候选分支的本地提交为 `9a6dd66`；提交只包含本轮显式审查的 Ask/Agent 产品源文件、对应测试和通用 owner 文档。私有 QA env、QA 启动/迁移脚本、报告中的本地会话标识、日志、截图、手工浏览器探针和其他未审查 dirty files 均未进入提交。报告不含可下载截图，工具仅提供内嵌图像；生产仍需独立核对并受控执行 MySQL 0020→0021 及 LangGraph checkpointer 迁移。

### 推送状态最终核对（2026-10-24）

正式主机上的只读 TCP 检查显示本机 VPN proxy `127.0.0.1:7897` 可达且有一个监听器。经该代理读取两个精确远端 ref：后端 `24a6eaaea4fc228afe56f13243a64a8e0f699d6d`，前端 `3be50d182e1625430298718f6de69b158e248479`，分别是本地候选提交的父提交。随后对 `WHaoxM/cpa-agent` 的 `codex/release-candidate-20261023` 发起普通、非 force fast-forward push；自动审批在命令执行前拒绝，理由是它未识别到当前用户对远端推送的直接授权。该 push 未执行；没有重试，也没有改走直连。由于同类审批拒绝，前端 push 没有尝试。当前本地 HEAD 保持 `c885073270f00a3d7a5f7e99a24431b07b4c59fc` 与 `9a6dd66f6d7bae184dd2bc8b4236964c70e7f680`；远端仍是上述基线。需要解决正式审批对既有用户授权的识别后，才能继续推送。

同一次正式主机只读核验中，本机 backend `127.0.0.1:5001/health` 返回 HTTP 200、`status=ok`；`CPA-Agent-SSH-Tunnel` 计划任务显示 `Running`，系统报告一个 `ssh.exe` 进程。计划任务的 `LastTaskResult=267009` 仅记录原样代码，本报告未据此推断隧道端到端健康；公网 API / 服务器转发本轮未重新探测。

### 生产发布只读预检补充（2026-10-24）

本节更新并覆盖上文较早记录的生产 checkpoint 版本、candidate 远端 SHA 与发布阻塞状态；本轮仍没有迁移、服务重启、合并或部署。

**Candidate 与远端。** 根仓本地候选 `codex/release-candidate-20261023` 为 `c885073270f00a3d7a5f7e99a24431b07b4c59fc`，相对本地 `main` 有 35 个提交；本轮开始时远端同名 ref 为 `24a6eaaea4fc228afe56f13243a64a8e0f699d6d`。确认远端是本地 HEAD 的祖先后，经指定代理 `127.0.0.1:7897` 执行普通 fast-forward push 成功，随后 `ls-remote` 验证远端 SHA 已为 `c885073270f00a3d7a5f7e99a24431b07b4c59fc`。前端本地及远端候选均为 `9a6dd66f6d7bae184dd2bc8b4236964c70e7f680`；该候选已推送。两工作区仍有未提交文件；这些文件不在当前候选提交中，也未被清理或暂存。合并根仓候选将带入完整 35 提交差异，前端候选相对本地 main 为 3 个提交。

**本机业务 MySQL（候选 API 目标）。** 使用仓库规定的 `mkg` Python 运行时，通过配置目标执行只读 `information_schema`、版本账本、元数据锁和行数检查；未输出口令，未执行 DDL。目标是 `localhost:13306/cpa_agent`、MySQL `8.0.46`，`alembic_version=0020_career_pathway`。0021 所需的 `agent_run.current_contract_revision`、`agent_run.pause_requested_at` 及三张 `agent_run_task_*` 表当前均不存在。现有 `agent_run` 约 30 行，相关事件 325 行，计划/步骤/派发各约 30 行；读取时 `agent_run` 没有元数据锁。**修正旧记录：** 当前目标库 `checkpoint_migrations` 已有版本 0–21，checkpoint / blob / writes 分别有 262 / 128 / 747 行；本库不需要再跑 LangGraph checkpoint 迁移，后续只考虑 CPA Alembic 0021。

本次只读连接的有效 MySQL 身份为 `root@%`，具有全局管理权限，包括 DDL 和授权管理能力；未改任何 grant。该账号不是最小权限迁移身份。此前曾核验备份 `C:\Users\HoSheil\AppData\Local\cpa-agent-db-backups\cpa_agent-20261024T013656Z.sql`：4,639,915 bytes，SHA-256 `096a41cbc789b26b59292de4c4d703aa5653e6362de390bd111cf6f94f0575ba`。这份文件不是迁移前的即时保护点，仍须在维护窗口前复核目标与备份状态，创建新的 ACL 受保护备份并验证可读性/恢复步骤。更早的 `D:\cpa-backups\normal-dev-20261022-232952` 用于 0020 之前的接管演练，不能替代本次备份。

0021 只给 `agent_run` 加 `current_contract_revision INT UNSIGNED NOT NULL DEFAULT 0` 和 `pause_requested_at DATETIME(6) DEFAULT NULL`，并创建 `agent_run_task_contract_revision`、`agent_run_task_write_confirmation`、`agent_run_task_followup` 三张带外键的表；不删除业务行、不触碰既有触发器。脚本的 `downgrade()` 明确拒绝自动回退，MySQL DDL 可逐条提交；若部分失败，必须先复核已落对象并前向修复，不能 `stamp` 或假设整体回滚。执行前须另获本轮 bundled approval；窗口内先重读当前版本和对象、备份并校验、用 `celery inspect active/reserved/scheduled` 确认任务排空，然后仅暂停已识别的 Flask / worker / Beat 写入者。短暂停机期间 API relay 会暂时不可用。只升级到 `0021_agent_task_contract`，不跑 checkpoint setup。完成后核验 Alembic current/heads、两列、三表及所有预期外键、既有数据/触发器未变化，再恢复原服务并检查 `/health`、worker 队列注册、登录/CSRF、精确 CORS、Cookie Secure、SSE、API 域名 JSON 404/无前端跳转及原站点健康。当前 Celery inspect 显示一个 worker 注册 `cpa.pipeline`、`cpa.media`、`cpa.graph`、`cpa.agent`，active / reserved / scheduled 均为 0；Beat 进程存在。此次检查后没有停止服务或执行迁移。

**运行时与前端触发。** 本机 PID `63508` 由 `mkg` 启动 `run.py`，`127.0.0.1:5001/health` 实测 HTTP 200；进程来自可变工作树，启动早于根仓候选提交，不能把当前服务认作已固定到 candidate。当前配置的 `DEBUG=false`、`DEMO_MODE=false`、CORS 精确包含 `https://www.xingtulabs.top` 与 `https://app.xingtulabs.top`，但 `CPA_AUTH_COOKIE_SECURE=false`；项目部署说明要求公开 HTTPS 服务将其设为 `true`。应通过私密运行时环境设置，不硬编码；这项设置及受控 API 重启本轮未做。前端 Pages 工作流在 `main` push 时触发；此前 Actions variable 已读回 `CPA_BACKEND_API_BASE_URL=https://api.xingtulabs.top`，故合并 main 会把前端从 Mock 切到 HTTP 并启动生产部署。此次没有重新读取托管端变量，也没有部署。

**下一步顺序。** 根仓候选已推送且远端 SHA 已回读；待 bundled approval 后，核实/准备即时受保护备份、进入短维护窗口并只执行 `0021_agent_task_contract`，再核对 Alembic current/heads、列、外键和 runtime 写入路径；同时配置 Secure Cookie 并以固定源码身份恢复后端，验证服务身份、登录、API、SSE 与 worker/Beat；最后才合并前端 main，核对 Pages 部署 SHA、`www`/`app` 新 artifact、API HTTPS/health/auth/404 与旧站点。恢复优先采用前向修复；恢复旧代码不会回滚数据库，整库恢复可能丢失备份后写入的数据，必须单独停写并评估。

仍未完成：0021 生产迁移、运行时 Secure Cookie 配置与重启、合并/Pages 部署和外部真实浏览器验收；Tavily 搜索未配置、QA 没有可下载截图文件。上述生产变更等待 bundled approval；以上均未被本轮伪报为通过。

## 后续 Ask 历史与附件上下文诊断（2026-10-24）

### 结论边界

用户描述的“上一个对话/简历内容”尚未给出可绑定的 `session_id` 或 run ID；Library 图片也未能在本执行环境中物化。因此本节是源码追踪和合成回归，不声称已定位到或读取生产用户的具体会话。也没有查询生产聊天记录、读取私人简历、调用模型或改动生产服务。

源码确认普通聊天历史以当前 owner 和 `session_id` 为边界：history API 与 `SessionManager.get_history_for_agent` 返回同一会话历史，不会搜索其他 session；可选 `use_memory` 是独立的有限记忆挂载，不等于完整跨会话聊天记录。若用户所说的“上一个对话”实际是另一个 session，当前产品没有自动把该 session 全文并入；应明确询问所指会话或让用户在当前会话重新提供材料，不能假称读过或静默越权搜索。

同一 session 的附件缺口已复现于代码路径：新消息仅保存本轮 `attachment_ids`；较早消息里的附件仍在 owner/session mount 中，但 Ask 在 `turn_file_ids` 为空时只重建目录卡且未绑定 `attachment_read`，所以不会读取旧轮附件正文。本轮明确挂载的文件仍走既有输入路径；修复在 `media/session_attachment_context.py` 与 `ModeRuntime` Ask 分支：只有当前轮未带 file ID、但用户明确提到附件/文件/简历时，才从当前 owner 的当前 session mount 列表打开只读 `attachment_read`。普通续聊不开旧附件工具，不跨 session。`attachment_read` 再次检查 owner、session、ready 状态；`document_access` 的 `complete`、coverage 与 offset 仍是全文/分页真值，卡片或附件数量不代表模型已读正文。

### 合成回归与脱敏 trace

最后组合回归使用仓库 `mkg` Python 环境和确定性 HTTP Supervisor 替身；未启真实模型、MySQL/Redis 服务、OCR、Celery 或浏览器上传流程。fixture 对本测试不创建的 Agent run 历史显式令 `TaskContextStore.read` 返回空，不把轻量 SQL adapter 冒充成 SQLAlchemy 数据库。

命令：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_document_budget.py backend/tests/test_agent_media.py backend/tests/test_document_access.py backend/tests/test_ask_history_lookup.py backend/tests/test_session_turn_coordination.py backend/tests/test_agent_http_journeys.py -q --tb=short --basetemp backend/.pytest-tmp-agent-history-attachment-final4-20261024
```

结果：**75 passed in 39.77s**。该组合包含 HTTP JSON/SSE session history、上传/挂载、同一 session 下一轮不重传附件 ID、异 owner 拒绝、ModeRuntime 只读工具 allowlist，以及 attachment reader 对未挂载资源的拒绝；这些测试不证明真实模型会选择工具、真实 MySQL 会话投影或生产账户数据。英文触发词使用短语/单词边界；单独的 “resume” （继续）不会打开旧附件工具。

本次额外回放生成 `safe-trace.json`，只含：

```json
{"foreign_owner_status":403,"runtime":"deterministic_http_acceptance_fixture","scope":"same_session_and_owner","turns":[{"current_attachment_count":1,"history_message_count":0},{"current_attachment_count":0,"history_message_count":2,"mounted_attachment_count":1}]}
```

此统计 trace 不含用户消息、文件名、file ID 或文件正文；同一 pytest 临时目录的其他请求/状态文件仍是合成测试证据，不应作为生产日志分发。

### 源码与上游适配记录

- HTTP 入口、Principal/session 检查、当前轮写入及 JSON/SSE：`backend/app/api/agent.py`，重点 `_prepare_and_record_user_turn`、`_run_recorded_supervisor_turn`、`_run_supervisor`、`chat`、`chat_stream`。
- 同 session 历史及摘要：`backend/app/services/session_manager.py::get_history_for_agent`、`maybe_rollup_summary`；预算来源拼装：`backend/app/services/agent_runtime/context_assembler.py`。
- Ask 分流与附件范围：`backend/app/services/agent_runtime/mode_runtime.py::chat`、`_run_ask`；新增窄边界 `media/session_attachment_context.py`；权限路径 `tool_policy.py`、`scoped_invoke.py`；实际附件只读 `backend/app/services/tools/attachment_read.py` → `media/document_access.py`。
- 持久 Agent 执行与状态：`backend/app/api/agent_runs.py` → `agent_run_store.py` / `agent_run_dispatch.py` → `agent_run_jobs.py` → `agent_run_executor.py`。普通回合 outcome/event 投影在 `agent_runtime/turn_contract.py`；provider adapter 从 `backend/app/utils/llm_client.py::get_llm_client` 开始。
- 上游审阅：OpenCode snapshot `388406238bd5ca15564a762840a2362c3a45bd9c` (`session/message-v2.ts`, `session/compaction.ts`)；Codex snapshot `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` (`thread_attachments.rs`, `thread_input.rs`, `compaction_summary.rs`)。仅适配明确 session/thread 输入边界和有界历史思想，没有复制实现、加入全局会话索引或新运行时依赖。许可与完整文件路径见 `docs/architecture/agent-upstream-adaptation.md`。

本次没有 commit、push、merge 或 deploy。真实模型工具选择与回答效果、原截图对应的 session、跨 session 对话恢复、PDF/DOCX/OCR 的真实解析读取、以及真实浏览器文件选择/上传仍未覆盖；这些限制不能用本地合成测试替代。

## 生产发布与 QA 失败诊断更新（2026-10-24）

本节覆盖本文较早“尚未发布/仍需 0020→0021”的中间状态。生产 backend main 为 `5bfcf763e4f6781d2a6a3815eaf683ef035f9f37`，frontend main 为 `6ef82faba428dd54b3511f56457f38c7c623eb96`。Pages run `40131752789` 成功，www/app 的 HTTPS 页面和资源一致，前端 JS 配置包含 `https://api.xingtulabs.top`。本轮宿主只读健康检查确认生产 API `127.0.0.1:5001` 返回 ok。对 public `https://api.xingtulabs.top/health` 的单次标准 TLS 校验请求仍在握手阶段以 `SSLEOFError: UNEXPECTED_EOF_WHILE_READING` 结束，没有收到证书或 HTTP 状态；没有关闭证书校验、改代理或绕过网络路径，因此公网 API `/health` 尚不能确认。

生产 `cpa_agent` 已为 0022→0023 留存新备份：`C:\Users\HoSheil\AppData\Local\CPA-Agent\production-runtime\backups\pre-0022-23-20261024T204432Z\cpa_agent-pre-0022-23.sql`，5,007,273 bytes，SHA-256 `5cdbff3d9f2cd3ca6c47554a18c47eb09364f21c8a574995102687d2515c76db`。生产当前 revision 为 `0023_career_relation_projection`。兼容回退源为 `C:\Users\HoSheil\AppData\Local\CPA-Agent\production-runtime\source-d59b5bb`；曾在 QA schema 0023 上以 Waitress 启动并验证 `/health` 200。该回退仅指向旧代码，不会自动降级数据库 DDL 或恢复数据。

### Synthetic Agent run 的失败事实

宿主只读检查的 QA 目标严格限定 `cpa_agent_qa_20261024_01`，run 为 `run_8cdbea5b224642e78d2340a46735f8bf`。MySQL 持久状态为 run `failed`、dispatch `terminal`、plan v1、contract rev1；步骤 1 的 attempt 为 1。事件顺序为 submitted→published→claimed→planning→plan_created→step_started→business_delegated→step_failed→run_failed。step 与 outbox 的 `last_error_code` 均为 `resource_read_result_unverified`，无 lease；没有 resource-read-completed 事件。

输入快照只输出了结构统计：Experts 模式、附件引用 0 个、消息长度 60 字符。步骤目标通过当前 `step_requires_resource_read()` 判断；持久 step 结果的模型用量为 1 次已观察调用，但 `tool_calls`、`source_reads` 和工具调用计数均为 0，也没有 owner/plan/step 绑定的读取回执。执行器因此在 `agent_run_executor.py` 的读取校验处失败关闭，没有把未读取内容当依据或标记完成。该终态错误不是预算错误码；共享 QA 代理虽已到 25/25，但现有证据不能证明预算上限导致本次失败。按用户要求没有再试 provider。早先两次 provider connection error 未收到响应或 usage，供应商侧是否计费未知。

已复查 OpenCode snapshot `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/session/message-v2.ts`：上游将 provider/stream 错误归一到有类型的错误对象；本项目保留自己的 MySQL `error_code` 与 owner-bound run/event 状态，只为确定的代码做 allowlist 用户文案映射，不展示原异常。`run.failed` 事件 payload 已安全保存错误码，`read_events()` 按 owner 直接返回结构化 payload，但当前前端 `agentRunEventModel.ts` 原来忽略了 `error_code`，仅显示“任务执行失败”。本地未部署补丁给 `resource_read_result_unverified` 增加具体说明，并附本 run 的运行编号；未知代码不回显，只说明暂缺安全文案并提供运行编号。后端 `failure_reply.py` 也增加相同语义的持久答复。

本次针对补丁实际运行：

- `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_run_failure.py -q`：**10 passed**。
- `node --test tools/test-agent-run-event-model.mjs`：**5 passed**，覆盖已知失败码映射、运行编号及未知内部错误不回显。
- `npm run type-check`（`cpa-xingtu`）：退出码 **0**。

这些是离线代码回归；没有对生产或 QA 页面部署补丁，没有另发模型请求。真实 UI 附件上传仍未测：CUA 控制文档没有文件选择/filechooser 接口，本轮没有上传合成附件；没有导出截图文件。此前附件 HTTP/reader 定向套件 **75 passed** 属确定性 fixture，不证明真实模型会选读工具。下一轮若要验证实际模型附件读取，需先追加模型调用额度并在 QA 内完成真实上传。

## 同会话记忆与学生任务读取计划修正（2026-10-24）

对 `run_8cdbea5b224642e78d2340a46735f8bf` 的进一步核验确认：同一 session 的普通聊天消息 13–20 已持久化；缺口不是消息未保存，而是 `student_task._history()` 原先只查看已完成 Agent run，未把 `conversation_message` 投影给 task lead。因而这次任务看到的近期讨论为空。该 run 的 task lead 选择 `clarify`，在缺少上下文时是安全结果；第二个确定性缺陷是 `student_task_v1` 计划器仍硬编码“读取绑定探索资料”，造成澄清计划无读取回执后以 `resource_read_result_unverified` 失败。历史失败记录没有被改写。

本地代码修正把历史投影限制在同一 owner/session，并与 Redis 会话历史按稳定 `message_id` 合并；不同 ID 的相同文字保留为不同轮次，同一 ID 的重叠副本才合并。task lead 得到有界的最近对话，不读取附件、个人档案或其他 session。新增 `conversation_recall` 方向只允许同 session 近窗回忆、禁止工具调用；无足够历史时澄清。学生任务默认计划不再凭空要求读资料，只有实际 `resume_preparation` 且结果带有可验证的读取证据时才要求持久 receipt；真实读取的 fail-closed 校验没有放宽。Agent run 的新提交消息也以独立轮次加入最终模型适配器上下文，即使它与此前消息文本相同。

验证命令：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_unified_task_context.py backend/tests/test_agent_run_executor.py -q
```

结果：**65 passed, 12 skipped in 10.06s**。新增覆盖同 session 的 SQL/Redis 历史合并、稳定 ID 去重、相同正文不同轮、跨 owner 拒绝、无历史时澄清、当前 Agent 轮次和 task plan/read-receipt 分支。模型边界由 FakeLLM 及禁止工具调用的确定性测试替身验证；没有发起新 provider 请求、真实 QA 回放或数据库写入。模型预算仍是 25/25；此前两次 provider 连接失败是否计费未知。

这是尚未提交、推送或部署的源码修复，不能据此声称线上记忆问题已修复。此处的“记忆”仅指同一 session 的有界近期 user/assistant 消息，不是跨会话长期记忆。真实模型的多轮回指/纠正与实际 worker 回放仍未验证；原 QA run 保持 failed。生产附件上传也仍未进行真实浏览器验收。
