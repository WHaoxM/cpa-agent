# 历史收藏与工具卡片真实性切片

## 参考与适配边界

- OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/session/message-v2.ts` 按 `part.state.status` 处理工具结果，正文仅作输出；本次沿用本项目 `build_ui_view` / `sanitize_tool_call_record` 薄适配，不复制上游运行时。
- Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/tools/context.rs` 将 `FunctionToolOutput.body` 与 `success: Option<bool>` 分开。借鉴结构化状态与内容分离；不采用其日志 `success.unwrap_or(true)` 作为业务完成依据。
- 收藏复用现有 Flask `RequestScope` 与 SQLAlchemy `engine.begin()` 事务及驱动 `lastrowid`，没有新建权限或存储框架。上游代码 Agent 没有本项目学生收藏表的 owner 适配能力。

## 行为

- `saved-jobs` POST 必须提供岗位引用，禁止生成虚构 `job_id`；返回本次 INSERT 得到并按 owner + ID 回读的 `saved_job_id`。目标职业同样返回 `target_role_id`。
- 收藏仍是用户提交的信息，不是可信岗位发布；显式 `source_verified:false`。岗位真实性由独立发布目录负责，不能把收藏成功当作岗位发布或匹配成功。
- 岗位目录新建的收藏使用 `POST /api/saved-jobs` 的 `publication_ref`（岗位公开标识、发布版本、内容摘要），服务端只据此查当前、仍有效且摘要正确的人工发布快照；客户端提交的名称、薪资、技能、匹配分和 `user_id` 一律不作为收藏事实。来源展示型 `source_csb_*` 岗位不能走这条路径。
- Alembic `0013_saved_job_pub_binding` 为 `saved_job.publication_id` 增加可空关联及 owner+发布记录唯一约束。重复收藏同一发布版本回到同一记录；旧的未绑定行不补造关联，读取时标记 `legacy_unverified`。撤回或后来出现新版本只改变历史状态，不删改所保存的不可变发布快照；新增收藏必须指向当前发布版本。
- `GET /api/saved-jobs` 返回 owner 范围内的保存记录和 `current` / `revoked` / `superseded` / `unavailable` / `legacy_unverified` 状态。绑定版本快照损坏或摘要不符时不把它作为可核对内容返回；`vacancy_validity` 始终为 `unknown`。页面不得从旧收藏的用户输入补回薪资、市场需求或匹配结论。
- DELETE 先按 Principal 查存在性，不存在/他人记录返回 404；执行后仍有记录返回 503，不报成功。历史目标职业删除使用同一边界。
- `DELETE /api/saved-jobs/records/<saved_job_id>` 只删除一条 owner 收藏；原 `DELETE /api/saved-jobs/<job_id>` 保留兼容，但可能删除该 owner 同岗位的多条旧记录。收藏页删除必须传记录 ID。
- 工具卡片不再从“错误/失败/查询结果/skills_count/task_”推断状态或任务引用。`running/pending/queued/needs_input/partial` 穿透两次 UI 清洗；没有结构化状态的记录为 `unverified`，纯文本工具结果为 `partial`。
- 工具 `done/completed` 显示“调用已结束”，不代表业务成果落库。任务引用必须来自结构化字段；权威任务查询仍须 owner 校验。
- 隐私正则脱敏保留，它不是语义判定规则。
- 公共 `graph_query` 改为结构化 JSON（status、row_count、source_id、message）；模型消费 message，UI 从计数字段渲染条数，两次清洗均保留。不再依赖“查询结果”字符串判成功。超过 10 条展示或字段截断显式 partial / truncated，并给出缩小范围重试路径。隐私摘要始终带“已脱敏”。

## 验证

在 mkg 环境运行 `python -B -m pytest -q backend/tests/test_historical_favorites_egress.py backend/tests/test_p0_scope_truth.py`。

测试经过 Flask 请求、Principal 门禁、仓库调用与序列化；覆盖匿名、伪造 owner、旧 ID 回读、删除零记录/删除失败，以及两个卡片出口对相同状态的连续处理。

2026-10-10 验证：上述切片 `25 passed`。另以临时 `accept_fav_*` Principal 通过 Flask test client 连接本机真实 MySQL，完成收藏和目标职业 POST → owner + 本次 INSERT ID 回读 → DELETE → 重复 DELETE 404；两类检查通过，临时记录已清理。此验证不替代宿主 HTTP 服务重启后的完整后端回归。

岗位目录收藏版本绑定的窄回归入口：`test_favorites_input_contract.py` 覆盖请求仅信任服务端版本引用、拒绝来源展示岗位、拒绝过期引用和记录级 owner 删除；`test_favorites_live_http.py::test_catalog_publication_favorite_real_mysql_is_owner_bound_and_historical` 使用随机 disposable MySQL schema 和 loopback Flask HTTP 验证权威快照派生、重复写幂等、跨用户读取/删除、撤回后历史回读和重新收藏冲突。Alembic 迁移通过 `test_mysql_migrations.py` 的真实临时 schema opt-in。这些检查本身不代表 Cookie 浏览器验收；实际页面收藏、退出重登、跨账号隔离、管理员撤回及历史回看另已完成，证据见[2026-10-21 收藏回访记录](../reports/2026-10-21-student-saved-job-return-replay.md)，后续入口由[用户旅程指南第七条](backend-user-journey-acceptance.md#第七条旅程学生收藏岗位后离开再回来继续看)维护。

## 未覆盖

本次不是全站历史入口验收。messages、ability 公共图的数据来源分区、roles 的 501 对话适配以及其他 ext 写入仍需逐项验收。旧的未绑定收藏重复行不自动合并；新版本绑定记录由 owner+publication 唯一约束和真实 MySQL 重放覆盖并发重试语义。删除仍按最终 owner 范围不存在核验，非审计日志意义上的精确 affected-row 证明。
