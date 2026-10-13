# P0 身份与结果真实性：本轮边界

本轮修复覆盖主 Agent 的 `pipeline_run`、`portrait_generate`、`report_generate`，以及 `/api/profile/student/upload`、`/api/report/*`、旧 `/api/pipeline/*`、`/api/ai/growth-analysis`、`/api/agent/career-suggestions` 和 `/api/learning` 的用户进度/推荐入口。它不宣称全平台用户资源 API 已完成审计。

## 运行契约

- `pipeline_run.params` 即使是模型可写 JSON 字符串，也先解码并经 `ToolScopeNormalizer` 按服务端 `GateContext.student_id` 归一化；工具执行时再次用门禁注入的顶层 `student_id` 覆写。缺 Principal 或访问非本人的上传路径即拒绝。
- Agent 管线复用 `PipelineRunner.PIPELINES` 的领域写入类，而不是旧的只计算预览的 `_run_l*` 辅助方法。`success:false` / `status:failed` 不会把 `pipeline_task` 写为 `completed`。L1 无技能写入时失败。
- `portrait_generate` / `report_generate` 的现有便捷函数只计算草稿，并未保存画像或报告行；因此标为 `partial`、`verified:false`，不返回虚构业务报告 ID。
- Agent artifact candidate 缺失状态或缺少工具侧 `verified:true` 时不会被提交为 `completed`，公开引用缺状态时也显示 `unverified`。当前 `verified` 仍是受信工具契约，不代替未来对每类资源做 owner/存在性数据库复核。
- `/api/profile/student/upload` 只返回 `PipelineRunner` 创建的 task ID，并把待确认候选标 `needs_input/partial`，不冒称资料已确认。`/api/report/generate` 使用 Principal 的 student ID；有报告行时须报告行与任务行均通过 owner 回读才返回报告 ID，无可信岗位来源而未生成报告时只返回已回读的任务 ID、`partial/success:false` 与空报告 ID。报告详情、导出、列表、进度和任务查询以 Principal 限制 SQL owner。
- 旧 `/api/pipeline/trigger` 的 demo 结果只构成预览，标 `partial` 而非已持久化的业务完成；`/status/<task_id>` 按 Principal 查任务。旧 L0 文件路径也限定在当前 Principal 上传目录。
- growth-analysis 的 `reportIds` 查询增加 owner 条件；career-suggestions、learning progress/recommend 均不使用 body/query/path 的外部 student/user ID 决定读写归属。旧 `/api/career/assessment` 也只用服务端 Principal，GET 在读取 Redis 缓存之前核对路径 owner；POST 写入后按 owner + assessment ID 回读，成功后失效旧缓存。学习进度 POST 回读 owner + chapter 并比较持久化课程、进度、完成状态，无法核验则不得显示成功。
- `/api/pipeline/ext/report/{full,html,match,career}` 与 `/portrait/student` 只以 Principal 为学生身份；旧计算器不落业务表，统一返回 `partial`、`verified:false`、`persisted:false`。临时生成的 `report_id` 改为 `draft_id`，HTML 内也标 `DRAFT-NOT-SAVED`。
- `/api/match` 的用户结果、技能差距、解释、职业推荐和列表路径先比较 Principal，再以同一 student ID 查询；`/run` 不再用请求体 student_id 或自造 task ID，而是调用 `PipelineRunner` 的 L3 并返回真实 task ID。L3 当前未取得可信岗位来源，`/run` 按返回状态标 `partial`/`success:false`，不得硬编码 completed。`/hybrid-candidates` 只读取调用者给的技能并检索公共候选，未作为用户资源入口处理。
- 历史 `/api/pipeline/ext/report/list|<id>/status` 和 `/exams|exams/<id>` 以 Principal 过滤报告/自评行；exam POST 只承认按 owner 回读的自评 ID，旧代码凭自评等级造出的 d3/d4 画像及无来源岗位回退已移除，返回 `portrait:null`、`portrait_ready:false`、`status:partial`。`/api/pipeline/ext/agent/session/save|stats|session/<id>/messages` 先核会话 owner，聚合统计按 owner 分区；写入后按本轮消息 ID 回读计数。`/api/graph/build` 用管线返回的真实 task ID 和 owner 回读状态，不再将 student ID 假充 task ID 或硬编码完成。

## 验证

补充管理面与向量检索审计：`/api/admin/*` 已统一要求 Principal 与服务端认证适配器设置的 `flask.g.cpa_admin=True`；普通学生身份、请求体或开发身份不能赋予管理员权限。无管理员适配器时管理面有意 fail closed 为 403。L1 构建任务的 `triggered_by` 来自 Principal，返回 task ID 前回读待执行任务行，不声称后台实际执行。

`/api/pipeline/ext/vector/search` 的 `all` 现在只查公共技能与岗位集合，私有 resume/conversation 或任意集合名拒绝访问。旧私有向量 owner 只在 JSON metadata 中，无法作为 ANN 查询的可靠标量过滤条件；个人向量写接口在校验 Principal/会话后仍返回 501，直到完成数据迁移与分区。公共向量写、init/rebuild 要服务端 admin claim；embedding 不可用时拒绝随机伪向量。

新负测：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_p0_admin_vector_scope.py backend/tests/test_demo_endpoints.py`。

`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_p0_scope_truth.py backend/tests/test_career_assessment_scope.py backend/tests/test_p0_legacy_ext_scope.py backend/tests/test_graph_api_public.py backend/tests/test_agent_runtime_isolation.py backend/tests/test_demo_endpoints.py -q`

测试覆盖跨账号嵌套 JSON 身份、非本人文件路径、`success:false` 失败状态、草稿非完成、REST 归属过滤与返回 ID。完整真实 MySQL/Neo4j/模型链路仍需 P1 逐段验收。

## 开源实现与适配边界

- OpenCode 快照 `388406238bd5ca15564a762840a2362c3a45bd9c`，`packages/opencode/src/tool/tool.ts`：参考其工具输入验证失败作为可区分错误的契约；本项目不引入 OpenCode 的 shell/文件能力或权限系统。
- Codex 快照 `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 仅作 Agent 运行时参考；此轮无需复制其基础设施。
- 本轮 legacy Agent session 读取/写入对照 OpenCode 同一快照 `packages/opencode/src/session/session.ts` 中 `Session.list` → `listByProject` 的项目作用域过滤，以及 Codex 同一快照 `codex-rs/core/src/client.rs` 中认证 owner 代际变化时清空会话路由缓存的隔离思想。二者不提供本项目 Flask `student_id` 的现成认证适配，故复用现有 `RequestScope` 与 MySQL `conversation.student_id` 作薄 owner 门禁，而不是复制上游会话存储。验收以实际 SQL owner 条件、跨 owner 负测和临时真实 MySQL 记录为准。
- 本项目沿用现有 `RequestScope`、`ToolScopeNormalizer`、`PipelineRunner` 和 `LangGraph` 工具适配层，未新建通用任务框架。

## 历史消息与公共能力图（本轮补充）

- `/api/messages` 的身份仅取 `RequestScope.student_id`。已读/软删写入后按同一 `(user_id, message_id)` 回读权威 MySQL 状态；公告不存在为 404，写后状态未核验为 503，不能返回假成功。已删除公告的“标记已读”不再清除删除状态。
- `/api/ability-graph` 是匿名可读的**公共岗位能力图**，不是个人画像图。岗位解析仅接受权威 `AbilityRole.role`、已导入 aliases 或精确岗位层节点名，不用双向子串猜测岗位；能力节点须由 `AbilityRole-[:HAS_NODE]` 锚定，跨岗位或缺少 role 的关系不可进入输出。无节点仍为 404，不伪称有图。
- 上游对照：OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/session/session.ts` (`listByProject`) 提供“查询先绑定可信作用域”的参考；Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/client.rs` 在 auth owner 变化时丢弃路由缓存。二者不提供 Flask Principal、MySQL 消息状态或 Neo4j 公共岗位分区协议，故本轮沿用已有 `RequestScope` 与数据库键/标签，未另造通用认证或图谱框架。
- 验证：`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_p0_messages_ability_scope.py`；真实 Flask 请求、MySQL 两账号隔离与删除保留、Neo4j 岗位关系分区和别名/子串行为。测试临时 ID 结束后删除。

## 未覆盖

管理面入口权限和历史向量接口的已修复部分，不等于逐项审计管理员操作目标、操作日志、来源发布及业务结果；个人向量原有数据仍需基础设施层保护和迁移。`/api/roles`、图谱聚合及其他 ext 写入仍需逐路由验证。

其余 `/api/pipeline/ext/*` 的写入、向量数据迁移与图谱聚合、`/api/roles` 及学习内容公共图仍需按 Principal → 业务行逐端点审计。`/api/career/paths`、`/transfer` 接收 `student_id` 查询参数但当前实现未实际消费该变量；它们属于公共岗位图读取，仍需在个性化接线时重新审计。旧 `/api/report/generate` demo fallback 仍能产生明显标记的演示报告，不能作为真实 L4 验收。历史 Agent session 保存仍是逐条提交，不是原子事务；写入中断可残留部分消息，但核验失败时不会报告完成。
