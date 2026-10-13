# Agent 提示词：先给可执行初稿，再按需澄清

## 现场问题与生效链路

复现输入：Agent 模式、没有画像/匹配/报告/附件，用户说“制定本周学习计划”。原回复把这些可选个性化资料全部列为启动规划的前置条件；即使 planner 给出纯文字建议，`ModeRuntime._run_agent` 仍因 `plan.current` 未获权威写回记录生成失败的 `plan_results`，`derive_turn_outcome` 将本轮标为 `partial`。前者是模型行为问题，后者是确定的运行时状态错误，不能只改前端文案。

当前实际路径：`mode_runtime._run_agent` → `agent.router.system`（`_llm_route`）→ `TaskPacket` → `scoped_invoke._build_subagent_system` 按 registry 合并 `agent.manual`、`agent.planner.system`、`agent.task_packet.system` → 用户回复/`derive_turn_outcome`。`agent.supervisor.system` 虽已注册，但不在这条 Agent 路由的 runtime prompt bundle 中，因此没有把修复押在那个文件。工作区只能覆盖 tone；本次行为契约写入不可由 tone 覆盖的 base。

## 开源来源与适配边界

| 已核查来源 | 可迁移的机制 | 本项目边界 |
| --- | --- | --- |
| [OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `session/system.ts`](https://github.com/anomalyco/opencode/blob/388406238bd5ca15564a762840a2362c3a45bd9c/packages/opencode/src/session/system.ts) 与 [`prompt/gpt-astra.txt`](https://github.com/anomalyco/opencode/blob/388406238bd5ca15564a762840a2362c3a45bd9c/packages/opencode/src/session/prompt/gpt-astra.txt)（MIT） | 运行时按模型选基座提示词；先根据上下文推进可完成的工作，歧义存在时仍可继续独立部分。 | 不复制编码 Agent 的工具/文件/自主执行规则，只借鉴“上下文优先、可逆工作先做”的决策顺序。|
| [Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `protocol/src/prompts/base_instructions/default.md`](https://github.com/openai/codex/blob/a06545b311fe01e51ce855c7aa5d8da21e9e7aaf/codex-rs/protocol/src/prompts/base_instructions/default.md)（Apache-2.0） | 指导回复明确假设、前提和下一步，优先可操作内容。 | Codex 是编码助手；职业规划的证据、分数、正式成果和持久化仍按本项目门禁，不移植执行拓扑。|

Trace 激活指向用户已确认的协作判断：有已知上下文时不重新盘问，只有缺失信息确实改变方向时才追问；明确执行请求应推进而非变成诊断问卷。这里把它转译为本项目的**窄规则**：一般建议先给初稿，可选资料只影响个性化精度；正式匹配/报告仍守证据和运行时门禁。Trace 是来源导航与判断边界，不代替当前仓库代码、测试和业务证据，也未据此保存或发布新的能力。

## 本次改造与验收

- 路由器按意图将学习计划交给 planner，不因没有画像/JD/附件先澄清；正式匹配/报告仍遵守前置门禁。
- 共享手册、planner、QA fallback 均区分“可用的暂定建议”和“有证据的个性化/正式成果”。planner 先输出少量本周任务、投入量、可核验产出与复盘办法；只在答案后问一个高影响问题，不编造个人缺口。
- **后续修正（用户指出未落库）**：planner 文字初稿不能仅凭回复标为业务完成。现由服务端把本轮正文写入 owner-scoped `agent_action_plan`，与 `agent_artifact` 索引同事务提交并回读核验；随后才写 `plan.current` pointer、公开 `artifact_ref`，失败则本轮为 `partial`。旧的“planner 不写 `plan.current` 但 turn completed”做法已撤销。
- `backend/tests/test_agent_prompt_provisional_plan.py` 检查无 owner 时不能声称已保存；`backend/tests/test_action_plan_persistence.py` 穿过路由、planner 回复、事务写入、黑板、PublicTurn 和 owner 查询，并验证索引写入失败时正文回滚。真实模型质量仍需按样本评估。

2026-10-10 提示词阶段曾以真实 `qwen-plus`、无学生身份执行，得到四步计划，但无权威 owner 因而未落库；这暴露了当时的验收缺口。修复后，以隔离的开发 Principal、真实 MySQL/Redis、Flask `POST /api/agent/chat` 执行同一输入：`200`、`planner_subagent`、`completed`、`artifacts=1`；本人 `GET /api/agent/artifacts/<id>` 返回完整正文，不同 owner 返回 `404`。测试数据已按 plan/artifact/session id 清理。详见 `docs/guides/agent-plan-persistence.md`。

回放：在仓库根目录用 `mkg` Python 执行 `python -B -m pytest backend/tests/test_agent_prompt_provisional_plan.py backend/tests/test_action_plan_persistence.py backend/tests/test_semantic_agent_routing.py backend/tests/test_prompt_selector_matrix.py backend/tests/test_agent_turn_contract.py -q`。真实模型验收应固定输入“制定本周学习计划”，空黑板/无附件，在已认证的 Agent 模式检查初稿、artifact 引用、按 owner 读取的正文及 turn `completed`；无 owner/存储失败必须是 `partial`。
