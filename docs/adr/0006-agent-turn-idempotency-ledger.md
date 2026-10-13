# ADR 0006：显式 Agent 回合使用 MySQL 幂等账本

- 状态：Accepted
- 日期：2026-10-11

## 背景

Redis 会话租约能阻止两个 worker 同时修改同一会话，却不能回答“这个 HTTP 请求是否已经执行过工具”。Redis 当前 AOF `everysec` 也存在最近写入丢失窗口。`conversation_message` 是 best-effort 投影，且不保存完整公开 `PublicTurn`，不能承担重放真值。

## 决定

默认 Supervisor 在调用方显式发送 `Idempotency-Key` 时，以 MySQL/InnoDB 表 `agent_turn_idempotency` 作为权威准入与终态重放账本：

- 唯一命名空间是可信 `(student_id, session_id, key hash)`；
- session 另有服务端持久 `incarnation_id` 纳入 request hash；completed replay 在 session lane 内权威核验存在、owner 与 incarnation，删除/重建不能借 worker cache 重放旧结果；
- 相同语义请求按 `reserved → running → completed` 结算；
- 相同 key、不同 payload 明确冲突；
- stale 或结算不确定转 `unknown`，不自动重执行；
- 公开响应过期后清正文但保留永久 `expired` 墓碑；
- Redis 继续承担 session STM 与跨 worker lease。

## 后果

- keyed 请求在 MySQL 不可用时 fail closed，不能降级成普通执行。
- 这是 at-most-once 偏保守的边界：即便协调失败发生在工具调用前，已准入 key 也可能成为 unknown。
- MySQL、Redis 与外部工具不是同一事务；本决定不承诺 exactly-once、rollback 或自动恢复。
- 未提供 key 的旧调用维持兼容行为，但没有上述保护。
- 部署必须显式执行 additive migration；请求路径不得临时建表。

详细协议、运维和验收见 `docs/guides/agent-turn-idempotency.md`。
