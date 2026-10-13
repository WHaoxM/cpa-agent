# Agent 语义路由去硬编码（2026-10-09）

## 本轮运行链与参考实现

范围是 `/api/agent` 所用 `SupervisorService → ModeRuntime → Ask / Agent`。运行链先前在 `fast_path.py` 靠问候、能力、报告、岗位、文档全文的正则和关键词决定答复或工具拓扑，`modes.py` 又用任务词决定 Ask 的升级提示。这些自然语言分类会误判否定句、同义表达和复合任务，且绕过主模型。本轮删除了这些语义表，仅保留旧前端合成前缀的协议正规化。

上游参考：OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` (MIT) `packages/opencode/src/session/prompt.ts` 的模型回合、工具 Schema 与结构化最终输出；Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` (Apache-2.0) 的模型/工具主循环及显式预算边界。二者不是 Python 运行时依赖；没有复制 TypeScript/Rust 路由代码。本项目直接复用已接入的 `LLMClient.chat_json`、Prompt Registry、模型窗口预检/usage ledger 和现有 LangGraph 子 Agent，只保留严格决策白名单薄适配。

## 当前契约

| 输入 / 状态 | 执行 | 失败和预算边界 |
| --- | --- | --- |
| Ask 普通文本（包括问候、能力、执行请求） | Ask 模型按 `agent.ask.system` 说明能力与限制；没有关键词直返固定回复，也不自动执行工具 | LLM 失败仍走既有可用性降级；不写画像/匹配/报告 |
| Agent 文本 | 每轮 `agent.router.system` → `LLMClient.chat_json` → 严格允许的 `call/clarify`、`profile/match/report/planner/qa`；业务 gate 与 owner 校验不变 | JSON 无效、模型失败或超预算时由 QA 子 Agent 正常回答，允许工具，不根据字面关键词选择危险业务子 Agent |
| 已完整挂载文档 | 同一结构化路由判断 `document_view=full/answer`；仅明确要逐字正文且文档服务标记 complete 时走已有脱敏直出 | 不完整、路由失败或预算不足时走附件问答/分页工具，不能假称全文 |

`chat_json` 使用模型厂商 JSON-object 协议，`LLMClient` 对每个 failover 重新做窗口预检并记录 usage；路由保留返回 Schema 校验，不把模型 JSON 当授权或业务完成证明。`confidence` 仅观察，不再以任意阈值覆盖结果。

## 剩余硬编码矩阵（不等于全平台完成）

| 模块 | 状态 | 下一步判断 |
| --- | --- | --- |
| `fast_path.py` 问候、能力、职业关键词、文档正则 | 本轮移除 | 旧函数不可再引用；测实际 ModeRuntime |
| `modes.py` Ask 任务词升级提示 | 本轮移除 | Ask 仅显式 mode，模型用产品语言提示可执行模式 |
| `experts_lead.py` 模板/槽位 hint、fallback 文案 | 已移除词表与槽位正则 | 模型 JSON 决策失败返回 `lead_unavailable`；详见 [Experts 证据门](experts-structured-evidence.md) |
| `quality_gate.py` 回复模板字符串判退 | 已移除 | 只留空输出结构检查；不声称已验证建议事实性 |
| `plan.py` `_maybe_heuristic_commit` | 已移除 | 本轮工具引用经 Principal、任务与业务行回读才可提交成果；纯文本结果保守 `partial` |
| `tool_result_channels.py` 正文错误词与 task ID 正则 | 已移除 | 仅从结构字段取状态/引用，旧纯文本工具先标 `partial`，待迁移结构化协议 |
| `tool_egress.py` 等展示摘要文字启发式 | **仍在** | 单独审计其是否触及业务状态，不能把本轮范围说成全平台硬编码清零 |
| 文档/画像/技能解析其他规则表 | 由独立模型负责审计 | 不与解析模型同时编辑；须逐条区别事实验证和语义猜测 |
| 权限、Principal、状态机 Schema、PII 脱敏、文档 completeness、评分公式 | **保留** | 这些是确定性安全/业务约束，不应交给语义模型“猜” |

## 复核

从仓库根目录以 `mkg` 环境运行：

```powershell
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_semantic_agent_routing.py backend/tests/test_agent_runtime_isolation.py backend/tests/test_fast_path_isolation.py backend/tests/test_agent_turn_contract.py backend/tests/test_document_access.py
```

以上是 mock provider 的运行链契约测试。真实 provider 的语义准确率、成本和复杂简历逐页质量仍须带有效 API key 和金标准集测量，不得以单测冒充生产质量。
