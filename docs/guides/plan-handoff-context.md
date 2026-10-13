# Experts 计划步骤的本轮目标交接

日期：2026-10-10。范围：`backend/app/services/agent_runtime/plan.py::build_plan_packets`。

## 故障与窄修复

此前仅第一步的 `TaskPacket.goal` 含 `handoff_brief`（其次 `goal_summary`、原话）。后续 lean 包不携带原话或历史，`goal` 只剩步骤编号和角色；若黑板只有业务指针，目标岗位、地点、期限等本轮约束在交接处丢失。现在每一步均携带同一份最多 400 字符的 brief，后续包依然不扩大 `read_keys`/`write_keys`、不复制原始历史。`scoped_invoke` 的 system TaskPacket 仍单独限 240 字符；完整有界 goal 进入 user 消息。此修复不解决 Lead 本身漏提约束或长于 400 字符的输入。

本改动选择性移入自独立 worktree commit `782fdb5d8f0c764051e1fa5289b87c91b4d265bc`；**没有**用旧版 `plan.py` 覆盖主工作树的 `run_plan(verify_updates=...)`。成功提交仍由当前服务端 `plan_evidence.verify_plan_updates` 根据 owner、本轮工具候选和业务行回读决定，模型黑板 JSON 不算完成。

## 开源参考与适配边界

- 本地 `opencode` commit `388406238bd5ca15564a762840a2362c3a45bd9c`，MIT，`packages/opencode/src/tool/task.ts`：任务 `prompt` 与 `subagent_type` 分离，任务正文经 `resolvePromptParts(params.prompt)` 交给子任务。只借鉴“角色不能代替任务输入”的显式交接，不复制其 TS/Effect 会话内核。
- `D:/AGeneral Workspace/_agent_reference/codex` commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，Apache-2.0，`codex-rs/core/src/tools/handlers/multi_agents/spawn.rs`：`input_items` 显式交给 `AgentInput::UserInput`；`tools/handlers/plan.rs` 只发 `PlanUpdate`。借鉴任务输入与 checklist/执行分离，不引入 Rust runtime。
- 现有 `scoped_invoke.py` 使用 LangGraph 图与 `ToolNode`；本修复只改变领域 TaskPacket 构造，没有造第二套 Agent 执行器或增加依赖。

## 回归证据与回放

修复前 `backend/tests/test_plan_handoff_context.py`：**13 failed / 4 passed**，失败均为后续步骤缺失 brief。修复后联合既有 Experts 计划门禁与权威证据测试：**47 passed**。这些是进程内契约测试，不代表 HTTP 验收。

`backend/tests/test_plan_handoff_http.py` 另启独立 Python 后端进程与真实 Werkzeug TCP 端口，经 `requests.Session` 发请求；不是 Flask `test_client`。成功路径通过 `/api/agent/session/create` → `/api/agent/chat`，执行真实 Supervisor → ModeRuntime → plan → scoped LangGraph → `verify_plan_updates`，检查 profile/match/report 三步模型实际收到相同的杭州岗位、本地与六周约束，并且 HTTP outcome 为 completed。拒绝路径只让模型输出伪造黑板 JSON、不调用 writer；权威验证拒绝后，HTTP outcome 非 completed 且不会运行 match/report。还覆盖无身份创建 401、异 owner 读取 403。

HTTP fixture 替换 Lead 决策、子步骤模型、pipeline writer、MySQL 行和 Redis 适配器；这些替身只用于确定性交接/门禁验证。`steps.jsonl` 是模型调用入口记录，不是业务生产日志。该 HTTP 测试**不证明**真实模型理解、真实 MySQL 事务、Neo4j、上传附件、SSE、跨 worker 恢复或职业建议质量。HTTP 响应的公开 egress 不暴露内部 `plan_results`；验收用 HTTP outcome/reply 与模型入口记录交叉核验，不把私有内部字段误当产品契约。

在仓库根目录执行：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
& 'D:/Miniconda/miniconda3/envs/mkg/python.exe' -B -m pytest -q backend/tests/test_plan_handoff_context.py backend/tests/test_experts_plan_gate.py backend/tests/test_experts_authoritative_evidence.py backend/tests/test_plan_handoff_http.py --tb=short
```

若 HTTP 失败，查看失败报告的 `tmp_path` 下 `requests.jsonl`、`steps.jsonl`、`server.log`、`ready.json`。每个用例启动新进程、随机端口和独立状态目录；`finally` 终止该子进程。不要用工作树单测绿灯替代 HTTP 结果，也不要把此替身验收写成真实业务成果已部署。
