# Experts：结构化决策与权威成果提交

维护对象：Agent 后端工程师。更新：2026-10-10。状态：可达运行时切片改造，未声称全平台语义硬编码清零。

## 可达路径与删除项

`ModeRuntime._run_experts → experts_lead_decide → _llm_lead` 使用既有模型路由、PromptSelector、LLMClient 和调用预算账本；返回结构化 JSON 后由原有枚举/模板白名单规范化。

Experts 的资料入口复用 ADR 0005 的共享 `EvidencePack`，不再另造专家资料副本：服务端按当前 Principal 发现并读取版本化资源，`DefaultContextAssembler` 将不超过 6000 字符的完整证据包原子地放入 Lead router context。超预算时整包替换为明确的不可用说明，绝不截出半段 JSON。该入口只提供计划事实：不授予工具或黑板写权限；`coverage.complete=false` / `has_more=true` 时必须保留 source、version 和 continuation，Lead 回答完成也不代表画像、匹配或报告完成。普通 Agent 暂不经此新入口，避免在本切片扩大运行面。

Lead 的结构化 JSON 调用只负责意图与执行政策判定，不再把 `explain.reply` 直接当作基于资料的最终答案。对于 `user_job=inform|revise` 且 EvidencePack 有事实项的非执行回合，运行时转交既有 Ask answer 模型做一次有界回答；服务端再确定性附加来源版本与分页覆盖。回答模型不可用时返回 `incomplete/partial + retry`，不会把 Lead 的“我将分析”式确认语发布为 completed。这里复用 LangGraph/Deep Agents 对 router 与 worker/answerer 的职责拆分，以及 OpenCode/Codex 的结构化宿主状态边界；没有增加关键词模板或第二套 Agent 循环。

- 删除中文关键词正则选择模板的 hint；`detect_plan_template` 仅保留兼容空返回，不再识别任何文本。
- 删除从“简历”“我的背景”“想做”等文字推断文件或目标已具备的逻辑。槽位只来自挂载文件引用和已验证黑板引用；登录 owner 链接不等于存在简历。
- 删除结构化模型失败后凭关键词编造执行/元问题意图。失败明确 `lead_unavailable`，不启动步骤，可重试。
- 删除回复模板关键词、长度、问题词汇重叠的语义质量判退；空输出等结构错误仍拒绝。简短合法答案不再被替换，事实性不靠该函数认证。
- 保留模板能力白名单、读写 ACL、缺实际资源的前置门禁、模型输出字段校验、token 预算等确定性边界；未把这些安全/Schema 约束交给模型猜测。

## 计划成果不再由正文或工具名决定

`ModeRuntime._run_experts → run_plan → verify_plan_updates` 是新提交边界：

1. `scoped_invoke` 的本轮可信 tool collector 提供实际 `pipeline_run` 结果中的 `pipeline_task` artifact；没有 artifact 的“已调用”摘要不算证据。
2. verifier 接收服务端绑定的 student_id，回读同 owner、本轮 artifact task_id、预期管线且 completed 的任务。
3. 画像核对业务行完整内容及 source_portrait_task_id；匹配核对来源任务和完整 JSON SHA-256；报告核对 owner、报告 ID、completed 状态和 sources。
4. 只有这些回读产生的引用才提交。模型自行写入黑板 JSON、长总结、包含/不包含“失败”等词、已有工具调用，以及会话旧成果都不能让本步骤完成。
5. incomplete、partial、跨 owner、内容被覆盖或来源缺失均停止；失败步骤经既有 plan_results 投影为非完成 TurnOutcome。学习规划尚无接入此 verifier 的权威持久化表，因此不能靠文案伪完成。

没有另建通用 Agent 状态机。`plan_evidence.py` 是已有 PipelineRunner/MySQL 业务记录的薄验证适配。

## 开源参考与适配边界

- opencode `388406238bd5ca15564a762840a2362c3a45bd9c`：`packages/opencode/src/session/prompt.ts#createStructuredOutputTool` 的结构化结果边界；`session/processor.ts#completeToolCall` 的实际运行调用与结构状态关联。Python 项目复用自己的 LLMClient JSON 输出与 LangGraph 执行链，不复制 TypeScript runtime。
- openai/codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`：`codex-rs/core/src/tools/approvals.rs`，可信宿主上下文不能被模型文字代替。职业画像/匹配 owner 验证属于本项目领域适配。

## 回归和剩余范围

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_experts_authoritative_evidence.py backend/tests/test_experts_plan_gate.py backend/tests/test_scoped_invoke_iteration_limit.py backend/tests/test_agent_runtime_isolation.py backend/tests/test_turn_outcome_status.py -q
```

覆盖模型无效模板不靠关键词补救、正反措辞不改变槽位、合法短答、当前任务→业务行回读、伪造黑板 JSON、旧成果、partial/failed、跨 owner、内容篡改、迭代上限。模型和 DB 以可观测替身验证调用适配，不代表真实模型职业建议质量通过。

后续窄修已覆盖普通 Agent 的业务黑板提交：同样调用服务端 verifier，未验证时通过 plan_results 标为非完成。兼容 gated_tools 已落库的 artifact_refs，仍回读其任务与业务行。`build_ui_view` 不再按“错误/失败/拒绝/不可用”正文判状态，不再 regex 从正文捞 task_id；pipeline_run 的结构化 task_id 只是待验证引用。gated_tools 的真实执行异常通过显式 error 状态传递，不依赖错误文字。

剩余：返回纯文本而不提供结构状态的历史工具统一投影为 partial，避免猜测业务成功；这也意味着纯文本成功工具在迁移之前会保守显示 partial。真正执行异常仍为 error。历史工具尚需逐个迁移；`tool_egress.py` 等摘要展示仍有文本启发式。推荐/学习规划持久成果及统一结构化工具结果尚未全覆盖；不以本切片宣称“所有硬编码已删除”。
