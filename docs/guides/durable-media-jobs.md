# 媒体长任务：持久投递与恢复边界

## 已接入的最小切片

只有 Agent 文件的 **PDF/DOCX ingest** 与旧 OCR 产物 **repair** 进入 `cpa.media` 队列。Flask 在保存原文件和 `agent_file` 元数据后发布 Celery 消息；worker 调用现有 `AgentFileService.complete_ingest/complete_upgrade`，没有另写一套解析器或业务任务表。

- Broker：现有 Redis，Compose 已启用 AOF。
- Worker：Celery 5.3.x，`acks_late=true`、`task_reject_on_worker_lost=true`、`prefetch=1`，单 worker 并发为 1。
- 权威状态：`backend/uploads/agent_files/<file_id>.meta.json` 与 MySQL `agent_file`；Celery result backend 未作为完成依据。
- 幂等边界：task id 由 `kind + file_id + attempt` 确定；READY 的 ingest 重投为 no-op；派生 artifacts 先落地后写 READY，数据库 artifacts 按 file_id 替换，避免重投追加重复行。Flask 查询每次优先读取共享磁盘目录，不能被进程内旧缓存卡在 `queued`。
- 明确失败：Broker 发布失败会把业务记录置为 `failed`，不会退回 HTTP 请求线程同步 OCR。
- 可重放事件：状态迁移与 `agent_file.ingest_meta.events` 在同一个权威记录中原子替换，不另写可能与业务状态分叉的 JSONL；事件只来自真实入队、worker 开始和成果提交，不使用定时器生成伪进度。

[Celery 5.3 task 文档](https://docs.celeryq.dev/en/v5.3.6/userguide/tasks.html)说明 late acknowledgement 可能导致任务重复执行，因此任务必须幂等；[配置文档](https://docs.celeryq.dev/en/v5.3.6/userguide/configuration.html)说明 worker 丢失是否重排还需 `task_reject_on_worker_lost`。本实现按这个边界适配，不把 Celery 的 `SUCCESS` 当作职业业务成果。

## 状态事件与 cursor 契约

文件上传/查询响应含 `event_cursor`，表示当前已持久化的最后一个事件序号。增量重放接口：

```http
GET /api/agent/files/<file_id>/events?after=0&limit=100
```

响应沿用 API 标准 envelope：以下字段位于 `{"success": true, "data": ...}` 的
`data` 内。

- Principal/owner 门禁与文件 GET 相同；不能读取他人的事件。
- `after` 是非负整数，返回 `seq > after`；默认 `0`。
- `limit` 为 `1..200`，默认 `100`。
- 响应 `cursor` 是本页最后一个序号；空页保持传入的 `after`。下一次把它原样传回。
- `has_more=true` 时继续读取；`latest_seq` 是当前权威记录的末序号。
- 每个事件字段为 `seq,event_id,status,phase,attempt,occurred_at,job_id`，失败事件另有 `error`。
- `status` 仅为 `queued|ingesting|ready|failed`；`phase` 仅为 `ingest|upgrade`。显式 repair retry 会增加 `attempt`，同一次 Celery late-ack 重投不会新增相同迁移。
- `snapshot` 是读取事件时的当前业务状态，用于首次渲染或发现 cursor 已追平；业务完成仍以 `snapshot.status`、文件 artifacts 和文件 GET 状态为准。

```json
{
  "file_id": "file_...",
  "events": [
    {"seq": 1, "status": "queued", "phase": "ingest", "attempt": 1},
    {"seq": 2, "status": "ingesting", "phase": "ingest", "attempt": 1}
  ],
  "cursor": 2,
  "has_more": true,
  "latest_seq": 3,
  "snapshot": {"status": "ready", "job_status": "completed"},
  "source": "agent_file.ingest_meta.events"
}
```

序号不连续、类型非法或超过保留上限时服务 **fail closed**，不跳过损坏项，也不根据当前状态伪造历史。当前上限为每文件 500 个事件；旧记录没有事件时返回空历史和当前 snapshot。它只是媒体解析事件流，不是全平台 CapabilityRun、SSE 或 AG-UI 接入声明。

AI 助手 HTTP 附件上传后立即保留 `file_id`，对 `queued/ingesting` 按 cursor 轮询此接口；`snapshot` 到达终态后再 GET 文件核验 `ready/failed` 及 excerpt/error。浏览器不再在固定 120 秒后伪造文件 `failed`。页面关闭或附件移除时停止轮询；连续查询错误显示为**状态同步失败**，不等同于后端解析失败。该 UI 接线不覆盖职途导航的画像管线任务，也不代表跨功能页任务事件已统一。

## 本机启动

本项目的 Flask 后端、Celery 媒体 worker 和真实 MinerU Sidecar 均运行在**宿主机 mkg 环境**；Docker 只提供 MySQL、Neo4j、Redis。先启动依赖：

```powershell
docker compose -p cpa-agent up -d mysql neo4j redis
```

然后在独立宿主机终端从 `backend/` 目录启动 worker；它与 Flask 共用本地 `backend/uploads`，不需要跨容器共享私人简历目录：

```powershell
$env:CPA_MEDIA_BROKER_URL='redis://localhost:16379/0'
$env:CPA_L0_PARSE_SIDECAR_URL='http://127.0.0.1:9380'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m celery -A app.services.agent_runtime.media.jobs:celery_app worker --loglevel=INFO --pool=solo --queues=cpa.media --concurrency=1
```

`.env` 的 `REDIS_URL` 也指向 `localhost:16379`。Flask 用宿主机 `mkg` 环境运行；本机严格模式测试可显式 `CPA_DEV_AUTH=1`，仅 loopback 注入开发 Principal。Celery AOF 持久化的是待处理消息，不代替业务文件状态/结果落盘。

扫描 PDF 还需要另启真实 OCR Sidecar：

```powershell
$env:MODELSCOPE_CACHE='D:/AGeneral Workspace/cpa-agent/models/mineru'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B backend/scripts/parse_sidecar_mineru.py
```

宿主机 worker 使用默认 `127.0.0.1` 即可；无需将 Sidecar 绑定 `0.0.0.0`。若以后另行设计容器 worker，需要先解决共享文件、依赖裁剪和网络身份，不把本轮构建试验算作已部署。

`docker-compose.parse-sidecar.yml --profile parse-sidecar-contract` 只启动 503 契约桩；健康响应明确为 `ocr_pdf=false,durable_tasks=false`，不得用于扫描简历验收。

## 可重放验证

1. 上传 PDF/DOCX，记录响应中的 `file_id`、`job_id`、`status=ingesting`、`job_status=queued`。
2. 在 worker 日志看到相同 task id 后停止宿主机 worker（不要删 Redis volume），再用上述命令重启。
3. 轮询文件 GET；late-ack 未确认任务应重新执行，并最终由业务记录收敛到 `status=ready,job_status=completed` 或带明确原因的 `failed`。
4. 对同一个已 READY 的 task id 手动重投/调用 worker body，确认不再次解析、不增加 artifact 行。
5. 停 Redis 后再上传一个 PDF，确认请求不运行 OCR，记录为 `failed` 且错误是排队失败；恢复 Redis 后走显式 retry。

单元回归：

```powershell
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_agent_media_async.py backend/tests/test_agent_media.py backend/tests/test_document_access.py
```

## 尚未宣称完成

- 本机 Docker MySQL、Neo4j、Redis 已运行，宿主机 Celery worker 与真实 MinerU Sidecar 已启动并通过健康检查；**尚未执行真实 Redis AOF + worker kill 的进程级故障注入**。
- 安装态 MinerU 3.0.7 虽有 `/tasks`，其 `AsyncTaskManager` 状态位于服务进程内；Sidecar 重启不提供可靠历史恢复，所以本项目未采用它代替 Celery。新版 MinerU 的官方部署资料同样把 API task manager 描述为进程内保留状态；采用新版前还需重新核对接口、许可证与实际恢复行为。
- worker 重投保证“至少一次”，不是“绝对只执行一次”；幂等业务提交仍是必需条件。
- 通用摘要、Reflection 等 `fork_jobs` 仍是进程内线程，本切片不把所有 Agent 后台任务升级成持久队列。
- 取消、优先级、多日人工审批、跨任务补偿以及真实 GPU/OCR SLA 尚未验收。
