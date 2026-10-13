# Agent 学习计划落库与功能页读取

## 根因与边界

此前 planner 的 `plan.current` 写入被关闭，`verify_plan_updates` 只核验画像/匹配/报告，行动计划没有权威正文表，HTTP 模式学习页也拒绝读取产物。仅把纯文字回复标记为 `completed` 会掩盖这个断点。

现在只有已绑定 `student_id` 与 `session_id` 的 Agent/Experts planner 回合可保存计划。模型只写自然语言正文；服务端清洗正文后将 `agent_action_plan` 与 `agent_artifact` 在一个 MySQL 事务中提交并回读。过短、超过 8000 字的正文不静默截断落库；对“无法制定计划、请补充信息”这类明确索要前置输入的回复也拒绝保存。`plan.current` 仅保存 plan/artifact id 与摘要，不保存正文；对话公开的 `artifact_ref` 从服务端提交结果产生，不接受模型自报 id。领域记录的 `draft` 表示建议尚待用户调整，artifact 的 `completed` 只表示本次**保存动作已完成**，不代表招聘结论经过事实核验。

`GET /api/agent/artifacts/<artifact_id>` 先按 Principal 查询索引，再按同 owner 与原 session 读取 `agent_action_plan.body_text` 并校验 SHA-256。前端 `continue_plan` 在 HTTP 模式把 artifact/session id 交给学习中心；学习中心按同一接口加载完整正文，刷新后仍可读取。Mock 模式保留原来的演示勾选组件；HTTP 模式不把浏览器临时勾选伪装成已落库进度，当前只展示真实计划正文。

## 开源对照与适配

沿用 `docs/architecture/agent-upstream-adaptation.md` 记录的 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（`packages/opencode/src/session/prompt.ts` 的 typed parts）与 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（显式状态/结果边界）的做法，仅复用“正文留在权威存储、对话传引用、状态由服务端核验”的契约。Python/Flask + MySQL 的 owner、事务与学习页读取仍是本项目的薄适配；不移植上游会话内核，也不把模型 JSON 当业务提交凭据。

## 回放

1. 用 `mkg` 运行 `python -B -m pytest backend/tests/test_action_plan_persistence.py backend/tests/test_agent_prompt_provisional_plan.py -q`。测试覆盖成功写入、HTTP 正文读取与跨 owner 拒读、`PublicTurn` artifact、索引失败时同事务回滚、拒绝把索要输入的回复/超长截断版本落库，以及无身份不能宣称已保存。
2. 启动 MySQL/Redis 与后端，在本地 strict 模式显式启用 `CPA_DEV_AUTH=1`；前端用 `npm run dev:backend`（5174，代理注入固定开发 Principal），不要拿普通 5173 登录页当作已认证联调面。
3. Agent 模式发送“制定本周学习计划”；响应应含 `action_plan` artifact 和 `completed` outcome。点击“打开学习计划”进入学习中心，刷新页面仍应显示同一份正文。用不同 Principal 请求该 artifact 应为 404。

2026-10-10 本机以合成 owner 通过真实 Flask、`qwen-plus`、MySQL/Redis 验证：`POST /api/agent/chat` 为 200/`completed`/1 个 artifact；本人 GET 为 200 且正文 327 字，不同 owner GET 为 404。合成 plan、artifact、session 记录随后删除。浏览器在 5174 后端联调模式完成了「发送 → 已完成行动计划卡片 → 打开学习中心 → 刷新后正文仍在」的实际点击链路；该页面仍只读正文，执行勾选进度未接后端。

联调时曾出现同一个请求有时 `partial`、有时 `completed`：检查到 Windows 上两个 `-B run.py` Flask 进程同时监听 `5001`，旧进程仍执行关闭 planner 写入的代码。停止旧进程并确认唯一 listener 后，同样的真实 HTTP 请求稳定产生 artifact。遇到“代码和单元测试已更新，但页面仍无产物”时先核对端口监听进程及启动时间，避免把旧进程响应误判为新代码缺陷。
