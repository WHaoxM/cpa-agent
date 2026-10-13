# Agent 同会话回合准入与跨 Worker 边界

## 现状与选择

`/api/agent/chat` 与 `/api/agent/chat/stream` 的 Supervisor 回合会读取、写入并结算同一份 Redis 会话状态。单进程的 `threading.Lock` 只能避免本进程线程交叉；两个 Flask/Gunicorn worker 分别持有自己的锁时，两个回合仍可读取相同历史并覆盖会话 context。

本切片复用已安装的 `redis` 5.x 客户端和已经是 session 权威缓存的 Redis 服务，而不新增队列或平行 session runtime：

- `SessionCoordinator` 先取得本地 mutex，再用 redis-py `Lock` 的 token/Lua release 建立以 session 哈希为 key 的 Redis 租约；watchdog 定期 `extend`。
- 每次拿到租约后，`SessionManager.refresh_session()` 从 Redis 重载会话；`redis_required` 使用不吞错的权威读取，key 缺失不回退 worker 旧缓存。否则第二个 worker 即使排队成功，也可能用旧缓存组装 history/context 并覆盖前一回合。
- `redis_required` 的 user STM 与 assistant+context 使用严格 Redis SET，成功后才更新本进程缓存；会话写入失败不公开 `completed`。模型完成后先核对服务端 lock token，再把 context 随 assistant 同一份会话状态写入，不能在失租时预写 context。
- 拿不到、续租失败、服务端 token 核对失败或权威读写失败时，公开 `SESSION_COORDINATION_UNAVAILABLE`。JSON 为 503；SSE 已建立连接后发送 error 事件，不伪造 `turn_done`。它不声称工具副作用可 exactly-once 重放。
- `docker-compose.prod.yml` 使用 `CPA_SESSION_TURN_LOCK_MODE=redis_required`，Redis 不可达时 fail closed；这才是跨 worker 保证。
- 开发默认 `redis_preferred`：Redis 失败回退本进程锁并记录 warning，**不提供跨 worker 保证**。可显式设为 `local`；多 worker 开发验证请使用 `redis_required`。

此设计只覆盖当前 request-bound Supervisor lane（prepare → STM → Supervisor → assistant settlement）。它不把旧 demo 引擎、后台 fork 任务或 LangGraph interrupt/checkpoint 宣称为可恢复的持久队列。

**失败与重试边界：** 未提供 `Idempotency-Key` 时，如果 user STM 已严格落地而模型或 assistant 写入失败，客户端重试仍是**新回合**，可产生第二条相同 user 消息。默认 Supervisor 的显式 keyed 请求现由 [Agent HTTP 回合幂等](agent-turn-idempotency.md) 提供 durable admission、终态重放与 unknown fail-closed；它也不会自动补写 assistant。工具调用与 MySQL ledger、Redis 会话 SET 不构成同一事务，因此仍不声称 exactly-once 或 rollback。遇到 unknown 必须回读会话与目标业务状态，不能盲目重复有副作用的请求。

## 已参考的成熟实现

- 本地 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`：`opencode/packages/core/src/session/input.ts` 对 session input 使用持久 admission/promotion 序列，`runner/llm.ts` 每次只 promote 一个 queued input。CPA-Agent 当前没有对应 durable input 表/事件投影，不能假装移植其恢复协议；本切片仅适配“同一活动 request 不交叉”的锁边界。
- redis-py 5.0.1 的 `Redis.lock(..., thread_local=False)`：提供 token 校验的 acquire/release/extend。watchdog 需跨线程续租，因此显式关闭 thread-local token。

## 验收

运行：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_session_turn_coordination.py
```

专属测试模拟两个独立 Coordinator（等价两个 worker）共享 Redis lock 后端，证明第二回合在第一回合结算前不能进入；并验证 required Redis 读/写不可用、key 消失、租约 token 丢失时 fail closed，assistant SET 失败时仅保留 user 而不泄漏新 context，preferred 模式只明确降为本进程锁。真实跨进程 Redis 故障/恢复仍需部署演练。
