# 审阅实体链接 → 个人图受控投影

状态：2026-10-10 已以临时 owner 在真实 MySQL + Neo4j 验证等价链接、拒绝撤销、
并发版本和新简历来源漂移；不代表所有真实用户简历已被审阅或投影。

## 边界与数据流

```text
已确认 L0 entity_linking 提议
  → Principal 下人工 POST entity-review
  → MySQL append-only review revision（审阅成功先提交）
  → 独立 projection_state pending/queued/retry_pending
  → 现有 cpa.graph Celery 队列
  → 校验当前确认 task + 当前审阅 revision + 原文 hash
  → 现有个人图 snapshot（必要时先用 personal_graph.publish_confirmed_personal_graph）
  → Neo4j 单事务替换该 owner 的 PersonalEntityMention 等价链接
  → MySQL projection_state completed（丢 ack 可安全重放）
```

只接受 `accept + equivalent`，且候选必须是仍存在、唯一、只有 `PublicSkill`
标签并具有 `repo:public-skills:v1` 来源和 `visibility=public` 的节点。
写入的是 owner-private `PersonalEntityMention` 节点及其到公共技能的
`REVIEWED_EQUIVALENT` 边，**不修改公共节点、原始简历或已确认 skills**。
`related` 只留 journal，不投影；`reject/defer` 不投影。新 revision 的单事务
替换会撤销同一私有快照内被拒绝的旧等价链接。新简历的旧快照仍在图中，
但旧快照不通过当前确认来源读门禁；历史清理需独立运维策略。

## 部署与重放

先执行以下两个显式 MySQL 迁移，不能在请求事务里 DDL：

- `backend/scripts/mysql/migration_resume_entity_review.sql`
- `backend/scripts/mysql/migration_personal_entity_link_projection.sql`

现有 `personal_graph_projection` 表和 Neo4j 公共技能种子也是前提。
`cpa.graph` worker 必须重启以加载新增的 `cpa.graph.project_entity_links` 任务。
本机仅重启了空闲的该 worker（未重启 `cpa.media`、MySQL 或 Neo4j）；
Celery inspect 回报该任务已注册，`CPA_TEST_LIVE_ENTITY_LINK_QUEUE=1`
的临时 owner 用例经真实 Redis 队列和独立 worker 从审阅提交到投影 completed
通过。另在 `cpa.graph` 的 Celery 5.3.6 `worker_ready` 信号上增加有界
journal 扫描（最多 1000 个最新 revision），避免升级期间任务丢失后永久 pending。
当前本机 graph worker PID 40112 已重启加载此钩子，inspect 为 ping pong、
`cpa.graph.project_entity_links` 已注册、active=[]。未单独模拟 worker 正在执行时
进程中断；持久重放仍应按下述 journal 扫描验证。
服务端 `GET /api/pipeline/entity-review` 的 `graph_publication_status`、
`graph_projection` 显示当前 revision 的持久投影状态。
投影表或 broker 不可用时审阅仍可存储，但返回 `not_available` 或
`retry_pending`；不可渲染为已发布。迁移/依赖恢复后调用
`replay_entity_link_projections(mysql, student_id=..., limit=...)` 以审阅 journal
扫描最新未完成 revision；旧 revision worker 会 `superseded`，不覆盖新结果。
只读 `query_reviewed_entity_links(client, owner_student_id=<Principal.student_id>, mysql=...)`
再次核对当前确认 snapshot、审阅 revision/摘要、投影 ack、私有节点 owner 与公共节点
来源；返回 `ready` 或明确 `unavailable`，不从旧混存 `Skill` 回退。
同一主 Agent 已通过现有 `graph_query(query_type=personal_entity_links, offset=0)`
薄适配此只读函数，身份只从服务端 Principal 取得；输入中伪造 `student_id`
不改变 owner。工具按十项分页并显式返回 `next_offset`，不把截断视为全文读取。
普通原始简历技能仍应使用 `career_data_read`，不能因新增图谱关系误走此模板。

## 复用依据

直接复用项目 `personal_graph.py` 的 `PersonalGraphSnapshot`、profile→task
行锁、Neo4j 官方 driver `execute_write` 事务，以及 `personal_graph_jobs.py`
的 Celery/持久状态/重放模式。OpenCode `388406238b` 的
`packages/schema/src/session-event.ts` 参考持久 aggregate/version 思想；
Codex `a06545b` 的 `codex-rs/protocol/src/approvals.rs` 是工具审批，
不是领域真值，本项目没有复制其执行权限模型。现有 LangGraph 1.2.9 的
checkpoint 用于长 Agent 执行，不为短审阅事务另建图运行时。

## 验收

`CPA_TEST_LIVE_ENTITY_LINK_GRAPH=1` 运行
`backend/tests/test_personal_entity_link_projection.py`，使用临时 owner、task
和当前已发布公共 Python 节点，finally 删除该 owner 的私有图及 MySQL 测试记录；
不会删改公共图。当前 3 个真实 MySQL + Neo4j 用例与 1 个真实 Celery worker 用例通过：等价投影及主 Agent
`graph_query` 入口、拒绝撤销、
旧 revision 与新确认来源不再投影。普通 `backend/tests/test_resume_entity_review.py`
验证 Principal、幂等、并发及 journal，不以投影完成替代审阅完成。

### 2026-10-10 具体 pending 恢复记录

隔离真实简历 owner 的 revision 1 审阅只有 `defer`，原状态持续 `pending`。
其 journal 创建时间早于新 graph worker 启动时间；检查时 graph 队列长度 0、
worker active/reserved 均空，而旧 worker 曾未注册新任务。现象与旧 worker
消费未知任务或提交后投递窗口中断都相符，**缺少对应 worker 日志，不能唯一归因**。
按 owner 调用现有 `replay_entity_link_projections` 后返回 queued，独立 worker
随后写入 completed、`equivalent_link_count=0`；同一宿主 5001 GET 返回
`revision=1,graph_publication_status=completed,mentions=40`。主 Agent
`graph_query(personal_entity_links)` 用该 owner 返回 completed/0 条/revision 1，
另一 owner 不可用。未修改 `student_001`。
