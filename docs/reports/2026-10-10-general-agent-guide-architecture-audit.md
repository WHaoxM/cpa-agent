# 通用 Agent 指南与 CPA-Agent 运行架构核验

Date: 2026-10-10
Status: 已完成一个输入边界切片；其余为待验证缺口，不是已交付声明

## 资料性质与核验方法

用户提供的 `D:/Downloads/Agent_0_1791642812318.pdf` 共 14 页，为手写/截图型材料，无可提取文本层。它是架构检查表，不是本仓的运行指令或可直接照搬的实现规格。三个独立模型分别从逐页目视、实际 Flask→Supervisor→ModeRuntime 链路、OpenCode/Codex 上游契约审阅，再由主执行者复核入口代码和 Flask 测试。裁决顺序是可达运行链及测试结果优先于已有规划文档。

| 指南要点 | PDF 页 | 当前事实与取舍 |
| --- | --- | --- |
| 输入/感知、意图、对话状态、规划、工具/流程、校验分层 | 2–4 | 当前主链已用 `agent.py → supervisor.py → mode_runtime.py → scoped_invoke.py` 和 LangGraph ToolNode；不新建“总编排 Agent”或第二套会话内核。 |
| Agent 决定下一步，Harness 负责循环、超时、重试、边界与记录；Skill/Framework 各有角色 | 6、14 | 保留现有 LangGraph、PreToolUse、预算、TurnOutcome 与领域服务。`session_runner.py` 等文件存在不等于它们已在主入口运行。 |
| 上下文按来源、阶段、窗口组织，摘要/截断不能假称全文 | 7–9、13 | `context_assembler.py`、Document Access 已有分槽及部分性契约；仍需核实摘要/记忆截断的公开标记。 |
| 工具 schema、错误码、失败恢复、权限边界 | 3、10、12 | `pre_tool_use.py` 与 `tool_result_channels.py` 已有门禁/双通道，但 `scoped_invoke.py::_retry_tool_node` 检查 `AIMessage` 而 ToolNode 实际返回 `ToolMessage`；当前所谓失败计数和超长工具输出截断分支不能据此认定有效。 |
| 轨迹、决策、成本、效率、失败快停可观测 | 11、13–14 | `turn_contract.py` 与预算账本已有部分契约；需要按真实请求核验每类工具失败到 HTTP/SSE/history 的一致性。 |

## 架构选择与开源适配边界

选择 **一条现有主 Agent 链 + 受控领域能力 + 共用输入/状态/证据/输出边界**。确定性画像、匹配与持久化仍由领域服务负责，模型负责意图、澄清、工具选择及解释；不要按 PDF 截图为每个功能复制一个 Agent。会话内编排继续用已运行的 LangGraph，长媒体任务继续用现有 Celery/Redis 与业务权威状态；不把泛化的“Flow Engine”重写为另一套本地执行器。

上游核验记录：本地 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT）的 `opencode/specs/v2/session.md`、`opencode/specs/v2/tools.md` 提供会话 admission、串行执行及工具生命周期参考；本地 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0）的 `codex-rs/core/src/rollout_budget.rs`、`codex-rs/core/src/compact.rs` 提供共享预算/压缩边界参考。本轮只借鉴可测试契约，不复制 TS/Rust 内核；Python/Flask/LangGraph、Principal/owner、职业规划证据和规则分仍是本项目适配边界。已有复用清单见 `docs/architecture/agent-upstream-adaptation.md`。

## 多模型候选与本轮执行

| 独立审阅 | 发现 | 决定 |
| --- | --- | --- |
| 指南逐页/主链审阅（GPT-6 Sol） | 工具失败观察分支读错消息类型；PDF 第 3、12 页要求结构化错误与恢复 | 列为下一 P0，避免和输入边界同时重写工具协议。 |
| 运行入口审阅（GPT-5.6 Terra） | 同步 `/chat` 接受并挂载附件/语音/资源引用；SSE `/chat/stream` 只收非空文本并忽略其他字段 | 本轮修复；这是可由同一请求在两个入口做差分验证的第一道输入边界。 |
| 上游协议审阅（GPT-6 Luna） | 保留现有 LangGraph；OpenCode durable session/tool lifecycle 可作恢复语义参考 | 不新增循环/队列；后续以崩溃窗测试决定是否深化持久化。 |

本轮在 `backend/app/api/agent.py` 提取两入口共用的“规范化一次→记录 STM→传 `prepared_turn`”边界。SSE 的 Supervisor 路径现支持纯附件/语音输入与 `attachment_ids/content_parts/voice_turn_id`。独立复核时又发现既有 `prepare_turn.py` 吞掉跨 owner 附件错误、`voice_turn.py` 按 ID 返回跨会话转写，以及同 session 在锁外先写 STM 的风险，因此同步修复：附件 owner 在 ready 状态前校验，跨 owner/缺失文件和语音引用在 STM/模型卡前拒绝；语音同时绑定 Principal 与 session；Ask 重建附件目录仍需传 owner。可恢复的已授权附件挂载错误仍由 ModeRuntime 写入 `orchestration.mount_errors`，公开 outcome 为 `partial`。Supervisor 入口在本机单进程锁内完成 prepare、用户 STM、执行、assistant 落库；SSE 先发连接注释，之后在锁外回放已完成事件。旧引擎对多模态返回 400，不声称支持该链路。

最小复验：在仓库根目录以 `mkg` 运行

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_turn_outcome_status.py backend/tests/test_agent_stream_parity.py backend/tests/test_agent_citation_contract.py
```

结果 `21 passed`。回归从 Flask 请求分别走 JSON/SSE，核对纯媒体回合 admission、一次 prepare、STM metadata、Supervisor 入参、可恢复挂载失败的 `partial` outcome；另走真实 `prepare_user_turn`（文件服务替身）、双 owner VoiceTurnService 和同 session 两线程回合顺序，验证跨 owner/跨 session 拒绝发生在 STM 与模型卡之前，语音创建缺 session 被拒绝。SSE 建连注释在 Supervisor 执行前送出；媒体拒绝在已建立连接上以脱敏 `error` 事件返回。另在 `test_agent_document_budget.py` 验证归属合法的 Ask 附件卡进入组装后的模型上下文。测试使用模拟 Supervisor，不证明完整 ModeRuntime/真实 LLM/OCR 的职业判断质量或多进程锁。SSE 当前仍在完整 Supervisor 回合结束后回放业务事件。

## 下一批需按同样方法验收的缺口

1. **P0 工具失败契约**：在 gated tool→ToolMessage→模型→UI/outcome 路径使用结构化 `status/error_code/retryable`，权限拒绝不重试，瞬时失败有界恢复；去掉依赖自然语言的伪失败检测。先测成功、异常、拒绝、超长结果，不因曾调用工具就标业务完成。
2. **P1 真实会话恢复/事件**：比较 LangGraph checkpoint 与 OpenCode durable lifecycle 契约，在“工具副作用已发生但 settlement 未提交”的崩溃窗证明不会盲目重放；SSE 只有在权威执行事件可重放后才称实时。
   当前 `SessionCoordinator` 是进程内锁，多 worker 的同会话互斥与持久 admission 尚未验收。
3. **P1 上下文完整性**：核查摘要/记忆被裁剪时是否显式标记并可继续读取；完整简历与检索片段分别验收，不将 OCR 覆盖率当事实正确率。
4. **P2 评测**：使用脱敏金路径记录模型/提示词版本、来源覆盖、工具结果、最终权威业务行和人工判分；比较时延、token、失败率、用户修正率，不从代码结构推断效果。

工作树原有大量未提交/未跟踪改动。本轮没有清理、重置或覆盖这些内容；只对上述输入/媒体归属/本机回合边界和其测试、文档做增量修改。
