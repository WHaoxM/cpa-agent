# 历史 Pipeline 启动入口的执行边界

2026-10-13 经完整 Flask 请求与真实 MySQL 核验：strict 模式的 `POST /api/pipeline/trigger`、`/l1/build-graph`、`/l2/generate-portrait`、`/l3/run-match`、`/l4/generate-report`，以及管理员 `POST /api/admin/pipeline/build`，原先只插入 `pipeline_task.status=pending`，不执行也不投递；等待后仍为 pending。管理员返回的普通用户状态链接还因 `student_id=NULL` 而是 404。故不能把“任务行存在”当作“已受理”。

当前只有 strict `POST /api/pipeline/l3/run-match` 恢复为**专用持久派发入口**。它仅接受 `job_portrait_id`，身份取登录 Principal；必须先通过已确认简历、当前 L2 画像来源与已发布岗位的服务端核验。前置条件不满足返回 409 且不建任务；通过后原子写入 `pipeline_task` 与 `pipeline_match_dispatch`，返回 202 和任务 ID。Broker 不可用仍保留 MySQL intent，返回 `delivery=retry_pending`，需运维重放。`GET /api/pipeline/list` 在 strict 模式只给 L3 标 `dedicated_async` 和专用 `start_path`。其他旧启动入口（含通用 `/trigger`、L1/L2/L4、管理员构建）仍在认证和最小校验后返回 HTTP 503、`success:false`、`retryable:false`，不写永久 pending。Demo `/trigger` 的 `partial` 仍只是预览。`/status/<id>` 与 `/events/<id>` 继续按 owner 读取 MySQL 权威状态。

这不关闭当前页面实际使用的 `/api/profile/student/pipeline`，也不关闭 `/api/match/run`、`/api/report/generate` 等同步且有领域校验的入口。`CareerNavigation` 当前调用 `runConfirmedStudentPipeline` 而非这些旧别名；前端 `triggerPipeline` 仅定义，未发现页面调用。不能因此推断实际 L0→L4 用户旅程已经完整。

## 重启这些入口之前的门槛

1. 明确各入口可信输入与来源：L2 使用已确认资料，L3/L4 绑定已发布岗位；管理员构建必须有真实数据集/知识库内容读取及对应 L1 构建服务，元数据 ID 不等于可构建内容。
2. L3 已复用 `backend/app/services/agent_runtime/media/jobs.py` 的 Celery/Redis 配置，向后台 `cpa.pipeline` 队列发送的 L3 消息只含任务 ID（该队列也可承载独立的 catalog 任务）。`pipeline_match_dispatch` 是 MySQL intent/lease/fencing 表；worker 原子领取并按同一 task ID 执行 `L3RunMatchPipeline`，未重复插任务。领域匹配行与 `pipeline_task` 终态同一事务提交；旧 worker 租约过期后即使继续运行，也不能以失效 token 写终态。
3. 数据库提交与 broker 发布间的故障窗口由 bounded replay 扫描补投；同一任务重复消息只能有一个 claim。运行中 worker 丢失后，15 分钟租约到期才可接管，恢复时会出现 `running→pending→running` 的真实事件。Celery Beat 独立进程默认每 60 秒向后台队列投递一次 `cpa.pipeline.replay`；worker 每次只扫描最多 50 条到期 intent，失败发布也记录尝试时间并按最久未尝试排序，避免固定前 50 条饿死后续 owner。**必须同时运行 Beat 与 worker**；只启动 Flask/Redis/worker 而没有 Beat，故障任务仍需显式运维重放。L1/L2/L4 仍须分别解决副作用幂等与自身来源约束，不得照搬 L3。
4. 管理员任务状态链接须指向管理员自身的状态入口，并定义其 owner/管理员可见边界；不能给出会 404 的用户状态入口。

## 开源参考及适配边界

- 本项目现有 Celery JSON 任务与稳定 ID：`backend/app/services/agent_runtime/media/jobs.py`。可复用 broker、late ack 和以业务 DB 为准的模式，但媒体任务的领域副作用/恢复协议不可直接套用到 L1–L4。
- OpenCode 本机快照 `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT），`packages/core/src/background-job.ts` 与 `packages/opencode/src/background/job.ts`：状态收敛与等待语义可参考；其注册表为进程内非持久状态，不能用作本项目可靠后台队列。
- Codex 本机参考快照 `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0；NOTICE 含第三方归属），`codex-rs/core/src/codex_thread.rs` 与 `thread_manager.rs` 主要处理交互线程/会话，非业务 DB 任务队列；不移植为 Pipeline worker。

## L3 部署、恢复与验收

> 现行结构迁移入口为 [MySQL 统一迁移](../runbooks/mysql-migrations.md) 的 `alembic upgrade head`；下文 `--install` 是原有部署记录，重放命令 `--replay` 仍有效。

提交后派发故障合同：`pipeline_task` 与 `pipeline_match_dispatch` 已原子提交后，即使 broker 发布、派发前的数据库回读或尝试标记失败，HTTP 仍返回 **202 + 原 task ID**。`delivery=retry_pending` 表示发布失败，`delivery=unverified` 表示提交后的派发读库/标记或其他异常；两者都应先按 owner 回读任务，再等 Beat 自动扫描或运维重放，不能返回无 task ID 的“未受理”503。只有前置条件失败或持久事务未确认提交时才拒绝创建。

在 `backend/` 用 `mkg` 环境先运行 `python -B -m app.services.pipeline_match_jobs --install`；新建 volume 的 `init_mysql.sql` 已含同表，既有 volume **必须显式迁移**。迁移缺失时 L3 创建失败返回 503，不会只留下孤立 pending。启动 `python -B -m celery -A app.services.agent_runtime.media.jobs:celery_app worker --pool=solo --concurrency=1 --queues=cpa.pipeline --loglevel=INFO`，并独立启动 `python -B -m celery -A app.services.agent_runtime.media.jobs:celery_app beat --loglevel=INFO`。二者与 Flask 必须使用同一 MySQL/Redis，Redis AOF 持续启用；默认扫描间隔 60 秒，可用 `CPA_PIPELINE_REPLAY_INTERVAL_SECONDS` 在 5–3600 秒内调整，固定 30 秒派发冷却。Beat/worker 故障期间恢复后会继续扫描；也可在故障处置时显式运行 `python -B -m app.services.pipeline_match_jobs --replay`，结果含 `attempted/dispatched/has_more/next_after_task_id`，有下一页时用 `--after-task-id` 接续。`retry_pending` 为派发状态，不是任务业务失败；业务状态只从 owner 的 `/api/pipeline/status/<task_id>` 与 `/events/<task_id>` 回读。失败原因在权威任务行，不信 Celery result backend。

本轮重放测试先红（模块不存在），安装迁移后 `CPA_TEST_LIVE_MYSQL=1` 运行 `test_pipeline_match_dispatch.py` 的 broker 失败重放、提交后读库异常、插入失败回滚、51 条 backlog 有界公平扫描及过期 claim fencing；再设置 `CPA_TEST_LIVE_MATCH_QUEUE=1`，用唯一合成已发布岗位/已确认简历/L2 画像，从完整 Flask 请求到独立 Celery worker/Beat 进程实际消费，分别验证正常投递、手动重放、无需手动命令的 pending 自动恢复及 expired running 自动接管。核对 `match_result`、`pipeline_task`、事件及跨 owner 404，测试精确清理夹具和随机队列。worker 测试仅替换了与派发可靠性无关的可选实体归一化/模型查找；实际 Celery、MySQL、L3 规则 scorer 与领域写入未替换。当前实际岗位发布目录为空，测试用合成发布不证明真实用户已有可匹配岗位；真实并发负载、进程硬杀时长和长任务超租约仍需独立压测。旧 `test_pipeline_dispatch_truth.py` 继续核验其余入口拒绝和 L3 前置拒绝无残留。
