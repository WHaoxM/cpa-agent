# Agent 运行时主链审查与迭代上限修复（2026-10-09）

## 范围与证据等级

本报告只审查用户实际对话主链和一次有回归测试的窄改造，不把仓库里名称像 OpenCode 的平行模块当作线上能力。入口为 `backend/app/api/agent.py` 的 chat/stream 路由，经 `SupervisorService.chat` → `ModeRuntime.chat` → Ask/Agent/Experts 分支；需要工具时最终走 `scoped_invoke` 里的 LangGraph `StateGraph`、`ToolNode` 和 gated tools。`subagent_graphs.py` 当前仍委托 `scoped_invoke`，不是另一套已运行的图内核。

`agent_loop_integration.py` 与 `session_runner.py` 有实现与互相引用，但在 `backend/app` 范围未找到 API 或 `ModeRuntime` 调用它们的入口。因此不能把它们的队列、会话中断、compaction 当作当前产品运行时已具备的能力。

## 上游参考与适配边界

| 参考 | 可核对模块 | 本轮使用方式 |
| --- | --- | --- |
| OpenCode 本地快照 `388406238bd5ca15564a762840a2362c3a45bd9c`，MIT | `opencode/packages/core/src/session/runner/max-steps.ts`；`opencode/packages/opencode/src/session/prompt.ts` 第 1281 行附近 | 借鉴达到步数上限时明确要求文字总结、不再请求工具的契约；保留 Python/LangGraph 图，不复制 TS 会话内核。仅靠提示词不可靠，所以在模型仍返回工具调用时由本地边界生成**明确未完成**回复。 |
| Codex 本地快照 `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，Apache-2.0 | `codex-rs/core/src/tools/handlers/get_context_remaining.rs`、`codex-rs/core/src/compact.rs`；通用预算/压缩参见 `docs/architecture/agent-upstream-adaptation.md` | 已对照其显式剩余上下文和压缩边界；此次不移植 Rust 执行/会话协议，也不声称覆盖 Codex 的工具事件协议。 |
| 现有项目 | `scoped_invoke.py`、`plan.py`、`mode_runtime.py` | 在当前可达链路做最小修复。职业画像、匹配、推荐、图谱业务仍留在项目业务工具和规则层。 |

## 窄问题：最后一步悄悄吞掉未执行工具

修复前，`scoped_invoke` 第 6 次调用仍使用 `bind_tools` 模型；若模型返回 `AIMessage(tool_calls=[...], content="")`，代码直接清空 `response.tool_calls`，从而使路由终止，结果落到“抱歉，子 Agent 未能生成回复”。模型刚请求的操作**没有执行**，但这个事实没有明确告诉用户。先前已有工具结果时，业务层仍可能根据 `tool_calls` 触发轻量画像/报告写回；仅从有工具历史推断任务完成是不成立的。

修复后：

1. 第 6 步向同一模型消息注入“不要再调用工具、总结未完成部分”的文字约束。此处只改善模型选择，不把 prompt 当成权限边界。
2. 如果模型仍返回工具调用或空文本，**不修改原 AIMessage**，也不执行该最后操作；图内生成明确的部分完成文本，告知最新操作尚未执行并建议用户继续。`scoped_invoke` 同时返回 `incomplete=true`、`stop_reason=tool_iteration_limit`。
3. `plan._maybe_heuristic_commit` 在 `incomplete=true` 时拒绝依据先前工具记录写出“已完成”的业务状态。

### 复验证据

`backend/tests/test_scoped_invoke_iteration_limit.py` 使用真实 LangGraph 图与 `ToolNode` 消息路径，替换的只有模型响应和一个无外部副作用的工具。连续六次工具调用请求中，前五次执行，最后一次不执行；第六次仍请求工具时最终回复含“尚未执行/继续”，结构化状态为 incomplete；第六次直接给文字时正常完成。另一个测试构造“已有工具证据 + 正面措辞 + incomplete=true”，证明不会误写黑板；同输入改为 `incomplete=false` 时正常提交。

在仓库根目录使用权威 `mkg` 运行时：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_scoped_invoke_iteration_limit.py backend/tests/test_agent_runtime_isolation.py backend/tests/test_context_engineering.py backend/tests/test_experts_plan_gate.py backend/tests/test_agent_turn_contract.py
```

结果：`54 passed`。这是模拟模型的**真实图执行路径测试**，不是线上模型或最终 HTTP/SSE 链路的语义质量证明。

## 后续架构顺序（不是本轮已完成声明）

| 优先级 | 当前风险/证据 | 建议采用的成熟边界 |
| --- | --- | --- |
| P0 | `ModeRuntime._pack` 未将 `scoped_invoke` 的 `incomplete/stop_reason` 暴露为公共结构化状态；目前用户只看见文字告知。 | 先统一 turn outcome 契约：completed / needs_input / partial / failed，以及可恢复 cursor；HTTP 与 SSE 相同语义，客户端不从文案猜状态。 |
| P0 | `_retry_tool_node` 遍历 `AIMessage` 判定工具失败/截断，而 LangGraph `ToolNode` 返回的是 `ToolMessage`；“tool retry”注释不是可证明的重试机制。 | 先在 `ToolMessage` 与 gated tool 边界引入结构化状态与错误类别，再利用 LangGraph 已有节点/重试原语；不要继续按中文关键词判错。单测必须覆盖工具异常→模型观察到错误→重试或停下的完整链。 |
| P1 | OCR upgrade 与会话后台任务仍在进程内 daemon thread 中；状态虽可查/重试，进程重启不能自动续跑。 | 比较现有 MinerU 异步任务接口或成熟持久队列，做到任务 ID、幂等、取消、超时、重试与权威状态收敛；不要扩造第二个本地调度器。 |
| P1 | 当前 Ask、Agent、Experts 与图谱工具不是一条统一“输入→工具→证据→结果”可观察协议；图谱工具也需要同样的 ACL、来源与部分结果语义。 | 不新增独立图谱子 Agent，保持现有 topology freeze；图谱是同一主 Agent 可调用的领域能力。把可复用底座固定在 tool schema、PreToolUse、模型/用户双通道结果、预算与 turn outcome 上；画像/图谱推理继续作为领域工具/有状态工作流。 |
| P2 | 用户交互的澄清、审批、执行中反馈、取消、断线恢复等仍缺完整 HTTP/SSE 验收。 | 先定义用户可见状态机和回合事件，再挑一条真实职业规划任务做端到端验收；在未接真库/真模型前只称 contract test。 |

不建议把 OpenCode/Codex 整个会话内核直接塞进 Flask：运行语言、工具权限与部署协议不同。优先复用它们已验证的契约与已有 LangGraph 原语，在本项目保留职业规划领域模型、图谱 schema 和确定性打分边界。
