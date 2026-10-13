# 实体对齐审阅契约（已实现持久审阅与受控个人图投影）

`GET/POST /api/pipeline/entity-review` 已接入当前已确认 L0 的
`profile_json.entity_linking`，只写独立 MySQL append-only 审阅记录。
部署时先执行 `backend/scripts/mysql/migration_resume_entity_review.sql`；
个人图投影另需 `backend/scripts/mysql/migration_personal_entity_link_projection.sql`。
审阅缺表返回 503，不会在请求中临时建表。审阅事务不会修改简历、skills
或公共图；提交后将当前 revision 交给既有 Celery 图谱队列投影，状态独立回读，
因此 `POST 200` 只表示**审阅已存储**，不表示个人图已发布。

## 用户输入

`GET /api/pipeline/entity-review` 返回当前 Principal 的已确认简历版本、
entity_linking 提议、revision、已审阅结果。资料不存在或未确认不得回退到默认用户。

成功响应为 `{ "success": true, "data": { ... } }`。`data` 包含
`source_confirmation_task_id`、`source_sha256`、`entity_linking_status`、
`entity_linking_reason`、`next_action`、`revision`、`replayed`、
`graph_publication_status`、可选 `graph_projection`、`mentions`、`reviews`。
有 revision 时投影状态可以是 `not_available`、`pending`、`queued`、
`retry_pending`、`completed` 或 `superseded`；仅 `completed` 代表当前 revision
私有图投影已提交。无审阅时为 `not_requested`。个人图只投影用户接受且模型提议
为 `equivalent` 的链接；`related` 保留在审阅 journal，不升级等价，也暂不投影；
`reject/defer` 不产生链接，后续审阅撤销等价时会移除当前私有快照旧链接。
`mentions` 的每项是 `{mention_index, mention, proposal, candidate, review}`；
`mention_index` 是当前确认版本内零起始且唯一的写入锚点，不能从报告 item ID 反推。
`proposal` 保持模型原始 `equivalent/related/unresolved` 判定及理由；
`candidate` 是该提议指向的服务端已检索候选；`review` 是最新用户审阅或 null。

`POST /api/pipeline/entity-review`：

```json
{
  "source_confirmation_task_id": "task_...",
  "source_sha256": "完整原文的SHA256",
  "request_id": "调用方生成的幂等请求ID",
  "expected_revision": 0,
  "reviews": [
    {"mention_index": 0, "action": "accept", "note": "确实指这个技能"},
    {"mention_index": 1, "action": "reject", "note": "同名但指另一项经历"},
    {"mention_index": 2, "action": "defer", "note": "之后补充说明"}
  ]
}
```

action 仅 accept/reject/defer；不允许客户端传 owner、任意 canonical_id、SQL/Cypher。
accept 必须指向服务器保存的合法 equivalent/related 提议；接受 related 不能变成 equivalent。
reject+note 保存纠错意图，但不把自由文本直接写入公共本体或改原始简历。
`reviews` 为 1–100 项，`note` 最多 500 字符。POST 严格拒绝 body 中额外字段，
包括客户端自造 owner、canonical_id、candidate。成功响应与 GET 的 `data`
形状一致；新提交 revision +1，同 request_id、同 payload 重放返回原审阅记录，
`replayed=true`。GET 总是返回当前最新记录。409 后应重新 GET，不用旧索引盲重试。

HTTP 状态：401 无 Principal；404 当前 Principal 无已确认简历；400 body 或审阅项无效；
409 确认来源、source hash、expected_revision、幂等键冲突或实体提议不可用；
503 存储/迁移故障且不声称保存成功。

## 权威存储

独立 append-only MySQL 审阅 journal，不能修改 L0 candidate 或已确认 profile_json，
否则破坏已有确认等值校验。记录 owner、确认task、source hash、request id、payload hash、
revision、审阅输入、完整最新审阅快照、创建时间。唯一约束为 owner/request_id 和
owner/source_task/revision。先写后回读，不以模型回复当写入成功。

事务锁序复用确认：student_profile → pipeline_task；锁内调用已有
require_confirmed_profile，再核对 task/hash 与 entity_linking 的 source_sha256。
新确认版本必须拒绝旧请求，不能自动迁移旧 mention_index。

同request_id同payload返回原记录；同request_id不同payload冲突；新request_id的
expected_revision 不符返回409。读/写身份只来自Principal，不能用请求JSON推断用户。

此短审阅事务本身没有后台执行步骤，不为它硬套 LangGraph。提交后实际的图谱
投影沿用现有 `personal_graph_jobs.py` 的 Celery broker、持久 ack 和重放模式，
由单独的 `personal_entity_link_jobs.py` 实现 revision 化投影；不能把审阅成功
假称图谱发布完成。运行与失败恢复见
`docs/guides/reviewed-entity-graph-projection.md`。

参考与适配边界：仓库既有 `backend/app/services/pipeline.py` 确认事务、
`pipeline_events.py` 的 `ConfirmationTransaction`、
`personal_graph.py` 的同序 row lock 是本审阅的直接实现依据。
已拉取 OpenCode `388406238b` 的
`opencode/packages/schema/src/session-event.ts` 使用 durable aggregate/version 的
事件协议思想；本业务审阅需要与已确认 L0 同一个 MySQL 事务和 owner 隔离，
故不复制其 TypeScript 会话事件运行时。Codex 参考仓库 `a06545b`
的 `codex-rs/protocol/src/approvals.rs` 处理工具执行审批，不等于用户对领域实体的
审阅真值，未复制为业务状态机。现有 `langgraph 1.2.9` +
`langgraph-checkpoint-mysql 3.0.0` 供长任务中断恢复；一次短 POST 不引入平行 checkpoint。

## 必须先完成的验收

- 无Principal 401；另一用户的确认task不可读写。
- stale task/hash、重复mention_index、越界索引、未知action、无提议accept失败关闭。
- 同幂等请求重放无重复事件；相同request_id不同内容409。
- 并发expected_revision仅一个请求成功；新确认与审阅锁序一致。
- MySQL故障不得返回已保存；重启回读结果一致。
- 原profile/candidate、skills、公共图写入数保持不变。
- 真实 MySQL 事务测试，不能只用内存 mock 宣称持久化完成。

`backend/tests/test_resume_entity_review.py` 通过临时 owner/task 写入、回读、
并发竞争与清理验证；不把测试中接受模型提议等同于标准实体发布。
