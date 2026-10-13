# Agent HTTP 回合幂等

## 适用范围

`POST /api/agent/chat` 与 `POST /api/agent/chat/stream` 的默认 Supervisor 支持显式 `Idempotency-Key`。调用方必须先创建 session，并在请求体传入 `session_id`；key 为 8–128 位字母、数字或 `_ . : -`。

不带 key 的调用保持原行为。`demo`、`react` 与 `langgraph_legacy` 不接受 key。本能力不是通用任务队列，也不覆盖后台 fork、媒体 worker 或领域 pipeline 自身的幂等协议。

浏览器预检允许 `Idempotency-Key`；重放响应带：

```http
Idempotency-Replayed: true
```

`Idempotency-Replayed` 与 `Retry-After` 已加入 CORS exposed headers。

## 权威数据流

```text
可信 Principal + 已解析 session owner
    → MySQL reserved admission
    → Redis session lease + 权威 session refresh
    → CAS reserved → running
    → user STM → Supervisor/model/tool
    → assistant + context 严格写入 Redis
    → CAS running → completed + 完整公开 HTTP envelope
    → JSON 返回，或 SSE 按已存 PublicTurn.events 输出
```

逻辑命名空间是可信 `(student_id, session_id, key)`；落库时 key 列保存 `SHA-256(JSON([student_id, session_id, key]))` 的 scoped digest，唯一键仍显式包含 student/session，原始 key 不入库。每次 session 创建还会生成并持久化服务端 `incarnation_id`；旧 Redis session 没有该字段时，以 owner/session/created_at 派生稳定兼容值。请求 hash 纳入这个权威 incarnation，并包含路由规范化后的语义字段（message、mode、interaction/plan、constraints、附件、content parts、voice turn、memory），排除 JSON/SSE 传输差异和客户端自报 owner。因此完成后的 JSON 请求可由 SSE 原样重放，且不会再次写 STM、调用模型或执行工具；删除后以同一字符串重建 session 时，旧 key 会 payload conflict，不会把旧 envelope 重放到新空会话，调用方须使用新 key。

completed replay 也会进入同一 session coordinator lane，并在返回前从权威 Redis 重新核验 session 存在、owner 与 incarnation；不能使用 worker 本地 cache。DELETE 使用同一 lane，因此 replay 与删除按租约顺序线性化：删除先完成则旧结果不可回放，replay 先完成则它在线性化点仍属于存在的 session。

即使部署全局仍是开发用 `redis_preferred`，显式 keyed 请求也会强制权威 Redis refresh 与严格 STM/assistant 写入；Redis 不可用时 fail closed。无 key 请求仍遵守部署原有的 coordinator 模式。生产环境继续要求 `redis_required`，以便 keyed 与非 keyed Supervisor 都不发生本地锁降级。

## 状态和公开结果

| 已有状态 | 相同语义请求 | 不同语义请求 |
|---|---|---|
| `reserved` / `running`，未 stale | `409 IDEMPOTENCY_IN_PROGRESS` | `409 IDEMPOTENCY_KEY_CONFLICT` |
| `reserved` / `running`，已 stale | 事务提交 `unknown` 后返回 `409 IDEMPOTENCY_OUTCOME_UNKNOWN` | conflict |
| `completed`，正文仍保留 | 原 envelope，200，带 replay header | conflict |
| `unknown` | `409 IDEMPOTENCY_OUTCOME_UNKNOWN` | conflict |
| `expired` | `409 IDEMPOTENCY_RESULT_EXPIRED` | conflict |

业务 `TurnOutcome.status=failed` 仍可成为 ledger 的 `completed`：它表示这个公开失败回合已经可靠结算并可原样重放，而不是执行结果未知。

若 admission 存储不可用，返回 `503 IDEMPOTENCY_STORAGE_UNAVAILABLE`，零 STM、零模型/工具调用。assistant 已结算但 ledger completion 不确定时不得返回普通成功；账本维持 running 或尽力转 unknown。同 key 永不据此自动再执行。

## 部署与清理

新库由 `backend/scripts/mysql/init_mysql.sql` 建表。已有 volume 必须在接收 keyed 流量前显式执行：

```powershell
Get-Content -Raw -Encoding UTF8 backend/scripts/mysql/migration_agent_turn_idempotency.sql |
  docker compose exec -T mysql mysql -uroot -p"$env:MYSQL_ROOT_PASSWORD" cpa_agent
```

也可在受控部署步骤用 Python helper `install_turn_idempotency_schema()`；请求路径不会自动 DDL。

配置：

```dotenv
CPA_AGENT_TURN_IDEMPOTENCY_STALE_SECONDS=7200
CPA_AGENT_TURN_IDEMPOTENCY_RETENTION_DAYS=7
```

`response_expires_at` 从可靠 completion 起计算，只是清理资格，不是自动定时任务。同 key 回读会懒过期；运维批处理可调用：

```powershell
$env:PYTHONPATH='backend'
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -c `
  "from app.services.agent_runtime.turn_idempotency import get_turn_idempotency_ledger; print(get_turn_idempotency_ledger().expire_completed_responses(limit=500))"
```

批处理只删除 completed 的公开 response body，并保留 `expired` 墓碑。墓碑会线性增长，这是防止旧 key 重新触发副作用的安全取舍；未来若做物理删除，必须先定义并公开 key 保证窗口。

## 失败与恢复边界

- `reserved` 在拿到 Redis lease 前遇到协调失败也会保守转 unknown；这可能封住一个实际零副作用的 key，是 at-most-once 偏保守取舍。
- worker 在工具副作用后退出：账本保持 running；stale 后由下一次同 key admission 原子转 unknown，不会接管执行。stale 表示“已无法继续证明原执行归属”，不是 worker 死亡证明；当前无 heartbeat，阈值必须大于允许的最长回合。仍在运行的超长回合被标 unknown 后，其 completion CAS 会失败，需要人工核对，不能贸然用新 key 并行重做。
- 完全未消费的 SSE response 可能只留下 reserved；stale 后同样转 unknown。
- unknown 的恢复动作是核对权威会话、工具目标业务行和成果物，再由业务/用户决定是否使用新 key；不能自动重试原 key。
- MySQL admission、Redis session 与外部工具没有跨系统事务。本实现不声称 exactly-once、工具 rollback 或任意 crash 自动续跑。

## 开源借鉴与适配边界

- OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT）：参考实验性 V2 `packages/core/src/session/input.ts` 的 durable admission、prompt admission exact retry 与 payload conflict 不变量；`specs/v2/schema-changelog.md` 明确将该 Session HTTP/V2 contract 标为 experimental。其 runner 仍将 durable multi-node ownership、最终输出持久化及工具 crash recovery 列为未完成，不能照搬为成熟终态幂等 runner。
- Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0）：参考 `codex-rs/protocol/src/turn_input.rs` 的“admission receipt 不等于 completion”，以及 `RecoverTurnRequest` / 受控 suspend+flush handoff 保留原 turn identity；它不表示任意 crash 自动恢复，也没有调用方 key 的 exact replay ledger。
- CPA-Agent 薄适配只使用现有 SQLAlchemy `Engine.begin()`、InnoDB UNIQUE/`SELECT FOR UPDATE`/CAS 和 Redis session lease；没有复制上游源码或新增通用队列。

## 真实 HTTP 验收

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q `
  backend/tests/test_agent_http_idempotency_acceptance.py
```

测试启动独立 Flask OS 进程，经真实 TCP、生产路由、真实 Redis DB 15 与真实 MySQL ledger 验证：

1. mutating tool 已提交后，JSON 跨 worker retry、JSON→SSE 与 fresh SSE→JSON replay 不产生第二次副作用；
2. 相同 key 不同 payload 冲突，STM 只有一组 user/assistant；
3. 两个 worker 并发同 key 时第二个得到 in-progress，完成后才可重放；
4. worker 在副作用后强制退出，stale retry 转 unknown 且副作用仍只有一次；
5. 副作用后 Redis assistant settlement 失败会立即落 unknown，重试不产生第二次副作用；
6. MySQL 不可达时 keyed 请求在 STM/tool 前 503；
7. `redis_preferred` 发生本地锁降级时，keyed 请求仍要求权威 Redis，在工具前 fail closed；
8. 删除后复用同一 session 字符串时，旧 incarnation 的 completed envelope 不会重放；另一 worker 即使缓存了被删 session 也不能重放；
9. 另一 owner 不能读取同 session 的完成结果。

fixture 只替换不确定模型及其外部工具，且为 crash 场景提供确定性进程退出；HTTP、scope、session、lease、ledger、egress 均走生产实现。测试显式装 migration，并在 `finally` 删除随机 session 的 ledger/Redis 数据。
