# L0 简历多 Agent 轨迹与统一预览

## 运行合同

`POST /api/profile/student/upload?async=1`（multipart `file`，可信 Principal）先在 MySQL 同一事务创建 `pipeline_task`、`pipeline_resume_dispatch` 和 `file_received`；返回 202 与可刷新回放的 `task_id`。Celery `cpa.pipeline.resume` 按租约/claim token 执行，Beat `cpa.pipeline.resume_replay` 负责有界恢复；旧同步上传合同保持不变。正式部署在维护窗口执行 Alembic `upgrade head`，由 `0004_resume_trace_schema` 接管两张表；已有数据库 volume 不会重放初始化 SQL。先前单独发布的 `migration_resume_trace.sql` 保留为历史兼容脚本，不再作为统一部署入口。

上传文件落盘后若 MySQL 回读确认意图未提交，接口返回 503 并删除该次新文件；若意图已提交但提交应答或 Redis 派发失败，保留文件与 `task_id`，返回 202、`delivery=retry_pending`，由恢复扫描重试。若数据库故障导致提交状态无法回读，接口返回 503 和 `data.task_id`、`delivery=verify_pending`，保留文件待核查；不能贸然删掉可能已被持久任务引用的文件，也不能伪报任务已创建。

异步 L0 是**按简历业务职责分工的串行协作**，并非四个模型同时投票：

| Agent | 真实工作 | 交接物 | 提交检查点 |
|---|---|---|---|
| DocumentAgent | 已有 L0FullParser 文件/OCR/文字块解析 | `DocumentArtifact` | 25% |
| StructureAgent | 已有 NSLE 结构模型，关闭其内联实体步骤 | canonical profile | 50% |
| EntityAgent | 已有原文支持的 mention 提取与实体对齐候选 | linked profile（仍待人工确认） | 75% |
| EvidenceAgent | 组织页块证据、已见/缺失页 | source evidence；保存候选后 100% | 100% |

`pipeline_task_trace` 是 owner-scoped、仅存白名单元数据的事务日志：阶段、actor、source→target、状态、检查点、模型名称/token 用量/耗时/段数等；不存原文、提示词、模型正文或联系方式。前端以游标调用 `GET /api/pipeline/trace/<task_id>`，原演示版中心预览与黑色系统仪表盘在原位切换动态状态；候选核对和正式画像仍使用各自权威业务面。百分比是**四个持久检查点完成比例**，不线性插值、不猜耗时；失败保留最后已完成比例和错误事件，不伪造 100%。旧 trace 没有独立 Agent handoff 时不能声称四 Agent 协作已被记录。

租约失效后由新 worker 接管时，claim 事务会一起将 `pipeline_task.progress` 重置为 0、将 `pipeline_resume_dispatch.attempts` 加一，并追加 `attempt_started` 轨迹；历史事件**不删除**。回放 API 的 `task.current_attempt` 为持久轮次（首次领取为 1，未领取为 0）；每个事件的 `attempt` 为所属轮次。`file_received` 是领取前上传收据，`attempt=null`；迁移前没有轮次标记的旧工作事件也为 `null`，不能冒充当前轮次。前端当前进度/Agent 面板只消费 `event.attempt === task.current_attempt` 的事件，历史可单独回看；游标跨页由后端回读游标前最近轮次标记，不能凭客户端这页有没有 `attempt_started` 推断轮次。`attempt_started.facts` 仅含轮次和是否恢复，非模型结果。

## 复用来源与适配边界

- 本地 OpenCode 快照 `388406238bd5ca15564a762840a2362c3a45bd9c`：`opencode/packages/opencode/src/event-v2-bridge.ts` 的事件/消费者分离，用作**仅输出内容安全事件、由 UI 重放**的设计参考；没有直接引入 OpenCode 运行时。
- 同一 OpenCode 快照的 `opencode/packages/opencode/src/session/processor.ts` 在重试状态里显式发布 `attempt`，避免把重试看成前一次执行的延续；这里只借鉴可观察轮次语义。其会话内存/Effect retry policy 不能代替本项目 MySQL 租约、Celery 至少一次投递和事务围栏。
- 本地 Codex 参考快照 `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`：与项目现有 `backend/app/services/agent_runtime/blackboard.py`、`task_packet.py`、`subagent_graphs.py` 一并检查。通用聊天子 Agent 的 prompt/工具权限和会话上下文不适合直接运行简历 L0，不能为了仪表盘捏造子 Agent 对话；这里复用现有 L0FullParser、NSLE、实体对齐和 L3 MySQL/Celery 派发/围栏模式，以薄的领域分工层传 artifact。
- 同一 Codex 快照 `codex-rs/core/src/client.rs` 的 `InferenceTraceAttempt` 为模型网络尝试单独记生命周期；本项目的轮次是**整次持久 L0 任务 claim**，不是一次模型请求，故不移植其请求级状态机，而在现有 dispatch/trace 事务中加轮次边界。
- 轨迹只证明上述后端操作及交接，不证明 OCR 逐字准确、学校档位可信、实体归一已发布、画像评分完成，或存在真实可匹配岗位。

## 验证与恢复

1. 本机 `mkg` 环境、MySQL/Redis 启动后，在维护窗口备份数据库、停写，从 `backend/` 对目标库执行 `python -B -m alembic -c alembic.ini upgrade head`，核对 `current` 与 `heads` 都为 `0004_resume_trace_schema`，并检查 `pipeline_resume_dispatch`、`pipeline_task_trace` 的列和索引后恢复服务。旧 `--install`/单独 SQL 不能替代版本账本。
2. 启动 Flask、`cpa.pipeline` solo worker 和独立 Beat。用测试账号上传仅含虚构姓名、学校、专业、项目的 `.txt` 到 `POST /api/profile/student/upload?async=1`；确认先返回 202、pending、0%。不要用真实学生账户做自动化回归。
3. 轮询该账号的 `GET /api/pipeline/trace/<task_id>`，核对 `source_extracted` 25、`structure_extracted` 50、`entity_resolved` 75、`candidate_saved` 100，以及三次 Agent handoff 的 `source/target`。再用另一个账号请求同一 task，应为 404；最后回读候选核对未确认的 `student_profile` 未被改写。租约恢复验收须让旧轮次先存 25% 事件、再使 lease 过期并接管，确认 API 返回新的 `current_attempt`、0%、新轮次 `attempt_started`，旧完成事件仍在历史且 `attempt` 不等于当前轮次；跨页 cursor 仍应正确标记后续事件。过期 worker 的写入必须被 claim token 拒绝。
4. worker 停止后任务保留在 MySQL；重启 worker/Beat 后扫描可重发，claim token 拒绝过期执行者。若 `failed`，从事件定位阶段并重新上传；不要只改前端百分比或把中间模型调用成功当作最终候选成功。

定向回归：`backend/tests/test_resume_trace_async_mysql.py`、`test_resume_golden_path.py`、`test_pipeline_status_cas.py`；前端 `cpa-xingtu/npm run build`，再用真实浏览器分别看空闲、处理中、候选核对、轨迹读取失败、窄屏状态。
