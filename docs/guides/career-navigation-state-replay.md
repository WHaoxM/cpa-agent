# 职途导航：已确认简历与画像任务的重放边界

## 为什么需要此接口

`GET /api/profile/student/candidate/<task_id>` 原先只接受 `needs_input` / `partial` L0，用户确认后任务转成 `completed`，同一深链刷新便得到 409；页面无法恢复已确认资料与后续 L2 状态。这是前端会话状态与权威业务状态断链，不是 PDF 正文少一页。

## 权威读取契约

- 服务端 Principal 决定 `student_id`。跨用户 task ID 一律 404，不读取其他用户简历。
- `needs_input` / `partial` 仍返回未确认候选。`completed` 仅当 L0 结果、当前 `student_profile._resume_confirmation.task_id` 和已确认候选回读一致时返回；被后续简历确认替代的旧 L0 返回 409。
- 已确认响应保留 `candidate_profile`、`source_evidence`，并附 `confirmed: true`、`profile_ready: true`。`portrait_task` 来自同一 owner、且 `result_json.source_confirmation_task_id` 等于该 L0 task ID 的最新 L2 持久任务；未生成则为 `null`。
- `portrait_task.status=partial` 时只展示已有的维度候选，缺失维度与总分继续为 `null`，`portrait_id` 不得伪造。旧 L2 中的 `next_action=confirm_semantic_facts` 只是缺口代码；当前没有语义事实确认写入 API，接口显式返回 `action_available=false`、`resume_state=not_available`，页面不得把它渲染为已可执行的恢复按钮。
- `completed` L2 还需通过任务引用解析器回读画像业务记录并校验 ID、内容与 Principal，才可作为已完成成果返回。进度事件仍由 `/api/pipeline/events/<task_id>` 从 MySQL 重放，不从前端计时器估算。
- `POST /api/profile/student/pipeline` 对已持久化的 `partial` 返回 HTTP 200、`success=true` 且 `data.status=partial`；这只表示请求成功，不表示画像/匹配/报告已完成。真正的鉴权、参数和执行错误继续使用 4xx/5xx。页面必须按业务 `status` 分支，不能以 HTTP 200 自动上屏总分。

## 开源参考及适配边界

在修改状态读取前比对了本地 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/session/message-v2.ts`（区分 pending/running/completed/error 的工具状态）及 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/thread_manager.rs`（从持久来源恢复而非 UI 内存）。二者均未被复制或引入运行依赖；本项目复用已有 `pipeline_task`、`student_profile`、`require_confirmed_profile`、`resolve_pipeline_task_ref` 和 MySQL 事件日志，薄适配到职业规划业务对象。上游许可证及其余模块见 `../architecture/agent-upstream-adaptation.md`。

## 验证

`backend/tests/test_resume_golden_path.py` 覆盖已确认深链重放、同源 partial L2 的已知维度、缺失总分、当前简历替代旧候选、跨 Principal 拒绝。真实 MySQL 的一条已确认三页简历回读：L0 `completed`，关联 L2 `partial`，有 6 个缺证语义字段且总分为 `null`；这是领域评分的证据不足，不是页面缺失。个人资料、原文与联系方式不记录在本指南。

宿主机 Flask 重启后，经 `http://127.0.0.1:5174` 开发代理回读同一 L0：HTTP 200、已确认、页 `[1,2,3]`、同源 L2 `partial`、D2/D4 有候选值而综合分仍为 `null`。直接请求后端时，其他 Principal 得到 404、无 Principal 得到 401。后端完整回归为 `618 passed`；前端 `npm run type-check` 与 `npm run build-only` 通过。浏览器控制连接不可用，本次不能把 API/构建结果冒充窄屏视觉截图验收。
