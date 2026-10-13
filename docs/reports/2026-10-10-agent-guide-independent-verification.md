# 通用 Agent 指南：独立核验补充

Date: 2026-10-10
Status: 对现有架构报告的只读源码复核与独立测试；不是完整端到端验收

> 本文件补充 `2026-10-10-general-agent-guide-architecture-audit.md`。只记录本轮实际复核到的证据，不覆盖主报告的协作历史或其他代理改动。项目目录含大量未提交/未跟踪文件；测试不写源文件，本轮未还原或清理工作树。

## 逐层差距：运行入口、可执行测试与证据等级

| 关注点 | 已有可核实能力 | 尚未验证 / 明确边界 |
| --- | --- | --- |
| 工具错误与恢复 | `backend/app/services/agent_runtime/tool_result_channels.py` 按结构化 payload 投影 UI `status`，传统自由文本不冒充成功；`turn_contract.py` 将 failed/partial/needs_input 映射到公开回合状态。 | `scoped_invoke.py::_retry_tool_node` 调用 LangGraph `ToolNode` 后遍历结果，却只筛 `AIMessage`；工具结果是 `ToolMessage`，因此这个节点里的失败计数和长输出裁切对实际工具结果无效。当前错误推断还依赖文本里的 `error/失败/traceback`，不能作为 retry 决策。尚无真实 provider 的 gated-tool 成功/异常/拒绝/超长响应完整黑盒矩阵。本轮未改此代码。 |
| 跨 worker 准入 | `/api/agent/chat` 与 `/chat/stream` 的新增边界测试覆盖同进程并发回合的串行顺序（主报告所记）。 | `agent_runtime/session_coordinator.py` 是 `threading.Lock` 的进程内锁；它不协调多个 Flask/Gunicorn worker。`session_input.py`、`session_runner.py` 也只是当前工作树中的源码文件；其 in-memory dict/单进程锁不构成持久 admission 或部署能力。没有两独立 worker/进程争用的黑盒试验，也没有数据库级 CAS/lease/唯一约束证据。不得称多 worker 已互斥或可恢复。 |
| 观测与评测 | `agent_runtime/budget.py` 有 request-local `ContextVar` provider usage ledger；`turn_contract.py` 统一部分公开 outcome；工具 UI projection 给出状态/摘要。 | 账本单测不等于生产脱敏 trace、决策链、来源覆盖、最终权威业务行与人工质量判分已经串联。未核实一套可运行的 gold-set 端到端效果评测；模型质量、证据正确率、成本/时延对照仍未验收。源码字段存在不能证明线上采集与留存。 |
| 预算与部分解析 | `budget.py` 对候选模型做窗口预检，未知窗口不默认放行；实际 provider usage 缺失、超限或 `finish_reason=length` 会 stop/partial。`document_access.py` / evidence packing 测试覆盖分页、预算、coverage 与 `has_more`/不完整标记。上下文摘要/记忆/附件 slot 本轮新增显式截断 marker 和 `context_truncated` outcome。 | 非 OpenAI tokenizer 是保守估计而非精确 token；非文本请求窗口估算可能未知。预算/分页验证多为替身模型与合成文档，不代表真实 OCR/多模态、超大真实附件或职业结论准确性。Compaction 对原始会话完整性的恢复路径仍未全链核验。 |
| 输入 / 意图 | 当前入口 `agent.py:/chat,/chat/stream → SupervisorService → ModeRuntime.chat`；媒体归一发生于 `prepare_user_turn`，intent/mode 依显式 mode 及 Lead/router 决策。可执行覆盖：`test_agent_stream_parity.py`、`test_semantic_agent_routing.py`。 | 测试替身主模型、主要是单回合 HTTP 与 mode 合同；未验证生产身份提供者、真实语音/OCR端到端效果或模型意图准确率。 |
| 规划 / 工作流 | Experts Lead → `plan.py` 有缺槽澄清、模板提示与工具/黑板门禁；覆盖 `test_experts_plan_gate.py`、`test_agent_prompt_provisional_plan.py`。 | 测试主要核对动作选择、状态和存储合同；不证明真实职业画像→匹配→报告链路的业务质量，也不应将 Prompt/模板列举视作每项都在主链可用。 |
| RAG / 工具 / 权限 | Ask/Experts EvidencePack 具来源、coverage、continuation，工具先由 PreToolUse/工具 allowlist 限制；`test_ask_evidence_surface.py`、`test_graph_query_tool.py`、`test_agent_runtime_isolation.py` 可执行。 | 工具错误 ToolMessage 分支存在并发审计缺陷（见上）；证据测试不等于来源真实新鲜或模型引用正确；图谱范围的公开降级不代表功能完整。仍需真实 gated tool 异常、拒绝、幂等及副作用 readback 的端到端验证。 |
| 安全 / 多租户 | 入口 Principal、附件/语音 owner 检查、PreToolUse allowlist、黑板单写者合同有相关用例；主报告本轮已验证输入 owner 差分。 | owner 路径测试用 stub 文件服务，不能替代 DB/Redis 真实多租户压测；单进程锁不等于跨 worker session 所有权/准入原子性。 |
| 上下文 / 压缩（本轮窄修复） | ModeRuntime 实际走 `DefaultContextAssembler`；此前 summary/memory/attachment slot 超预算只留下 `...`。本轮新增 marker：说明只有部分内容注入、无自动续读游标、不能据此声称资料不存在/已读完整；将 `context_truncation` 映射为 `partial/context_truncated`、`answer_in_session`（不是后台 continuation），`resume_state=not_available`。`project_turn` 只公开安全 slot id/outcome，不泄露 prompt marker 或摘要正文。 | `test_mode_runtime_marks_truncated_summary_as_partial_turn` 通过 ModeRuntime→assembler→outcome→public projection 验证；没有底层摘要/记忆源的 cursor，因此只能请求澄清或使用之后明确授权的独立来源读取。Compaction 是否完整保留每个实际来源与用户可见恢复路径仍未验收。 |
| 观测 / 成本 / 评测 | 回合投影与 provider usage ledger 有可执行测试；本轮补丁让被截断 slot 的回合状态可见。 | 观测并不包含可验证决策轨迹与 gold-set 人工评分；无证据证明生产采集已串联“模型/Prompt版本→证据覆盖→工具→权威业务落库→人工判分”。成本对账只在受测同步模型调用范围内，后台 fork 账本不在同一请求上下文。 |

## 独立测试记录

按项目 `AGENTS.md` 指定的 `mkg` 环境，在仓库根目录执行：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q `
  backend/tests/test_agent_call_budget_ledger.py `
  backend/tests/test_model_context_budget.py `
  backend/tests/test_agent_document_budget.py `
  backend/tests/test_ask_evidence_surface.py `
  backend/tests/test_turn_outcome_status.py
```

结果：**61 passed**，3 warnings（开发默认 `SECRET_KEY`、`pynvml` 弃用提示、`TRANSFORMERS_CACHE` 弃用提示）。另对本轮 context 切片执行：

```powershell
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q `
  backend/tests/test_context_assembler.py `
  backend/tests/test_turn_outcome_status.py `
  backend/tests/test_agent_document_budget.py
```

结果：**40 passed**，1 warning（开发默认 `SECRET_KEY`）。其中新增回归实际调用 `ModeRuntime._chat_impl`，对长摘要观察 assembler 输出 marker、最终 `context_truncation`、`partial/context_truncated` outcome 与 `resume_state=not_available`。后续再联合 `test_context_assembler.py`、`test_turn_outcome_status.py`、`test_agent_stream_parity.py` 执行 **27 passed**，验证安全 slot 标识通过公开投影、内部 marker/摘要正文不外泄，并确认 JSON/SSE outcome 投影回归仍绿。测试证明这条 Python runtime path 的状态投影，不证明真实模型遵守 marker、不证明底层数据可续读或人工输出质量。各组 targeted runs 分别执行，非一个全量套件。

独立源码检查发现 `scoped_invoke.py::_retry_tool_node` 的 `AIMessage`/`ToolMessage` 类型不匹配，足以否定“该节点目前已对 ToolNode 错误统计及截断有效”的说法。这里是静态的具体实现缺陷证据，尚未用一次真实工具失败请求端到端验证对应用户可见后果。

## 上游参考：精确快照、许可与移植边界

| 仓库快照 | 参考模块 | 可借鉴契约 / 不可宣称事项 |
| --- | --- | --- |
| 本地 `opencode/`，commit `388406238bd5ca15564a762840a2362c3a45bd9c`（2026-10-08），仓库 `LICENSE` 标注 MIT | `opencode/specs/v2/session.md`（admission inbox、PromptAdmitted/Prompted、按 Session 串行 runner、重启时将遗留 running tool 明确失败）；`opencode/packages/opencode/src/session/prompt.ts`（工具状态更新与 orphaned/interrupted 检查）；`opencode/specs/v2/tools.md` | 适合作为 durable session/tool lifecycle 的协议与恢复不变量参考。TypeScript/Effect 内核不是 CPA 运行依赖；适配应使用本地 SQL admission/幂等键/权威业务状态 + 现有 LangGraph checkpoint，先覆盖副作用与 settlement 的 crash window。规格文件和 checkout 存在不证明 CPA 已实现这些语义。 |
| 本地 `_agent_reference/codex/`，commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（2026-10-09），仓库 `LICENSE` 标注 Apache-2.0 | `codex-rs/core/src/rollout_budget.rs`（共享会话树预算记账/提醒状态）；`codex-rs/core/src/compact.rs`（压缩后历史替换与 checkpoint metadata）；`codex-rs/core/src/tools/handlers/get_context_remaining.rs` | 适合对照预算耗尽和 compaction 的显式状态/历史不变量。Rust runtime 不作为 Flask 依赖；借鉴时继续以 provider usage 为权威，明确估算方法与截断续读，不复制 rollout/session 核心。 |

上述许可只说明参考 checkout 中对应仓库声明，不自动覆盖依赖、模型、生成数据、补丁或部署组合的许可义务；复用代码时仍需逐文件保留 notices 并做兼容性审查。本轮只用作契约参考，未复制上游实现。

## 建议的下一步（不与当前并发代码编辑冲突）

1. 先补一个针对真实 `ToolMessage` 的单元回归：成功、工具抛异常、permission denied、超长结果分别确认 status、模型可见结果、UI outcome；retryable 与权限拒绝走不同策略。
2. 对跨 worker 准入做两独立进程竞争测试，并验证 admission idempotency、数据库唯一键/lease、同一 turn 的 STM 顺序；当前单进程 Flask 锁测试不能代替该验收。
3. 评测切片应记录脱敏模型/提示词版本、证据/附件覆盖、工具输入与结果、权威持久化结果及人工 rubric；分开报告 parser coverage、OCR CER 与建议正确率。
4. 摘要/记忆裁剪目前会暴露 partial；要提供真正继续读取，须为持久源增加可校验、身份绑定的 cursor，并通过真实请求/权限测试。不可将 `answer_in_session` 误读为自动 resume。对未知 tokenizer 与多模态输入保持“未知”，不可报精确剩余 token。
