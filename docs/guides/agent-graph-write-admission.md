# Agent 管线工具的图谱写入准入边界

日期：2026-10-10。范围：通用 Agent 指南第 3、10、12 页的工具权限与执行边界；不是图谱内核重写。

## 已复现的失败

原有 `pipeline_run` 对 `profile` / `match` 子 Agent 可见，参数允许 `L1_build_graph`。PreToolUse 将 `student_id` 绑定到当前 owner，但 L1 默认 `skill_skeleton` 仍把模型传入的 `portrait_id` 交给 `GraphStorage.save_job_portrait`。该 writer 用 `MERGE (j:JobPortrait {portrait_id: $portrait_id})` 更新共享节点，没有 owner 分区。

回归测试用随机 ID 创建隔离的 Neo4j `JobPortrait` 哨兵，owner 为 `victim_B`，名称为 `victim baseline`。以 `owner_A` 的 GateContext 通过实际 LangChain adapter、gated wrapper、PreToolUse、PipelineRunTool、L1BuildGraphPipeline、GraphStorage 调用，权威图行回读的名称变成 `model supplied overwrite`，owner 仍为 `victim_B`。修复前 live 用例确实失败；不是仅源码推测。

测试只替代了模型抽取输出和 MySQL task tracking，Neo4j 写入及最终回读为真实数据库操作。随机哨兵与同名测试技能在 `finally` 精确清理，不读写其他历史图数据。它不证明真实 LLM 会自发选择此调用，也不是完整 HTTP 主回合验收。

## 最小修正

- `pre_tool_use.py` 在现有 visibility 检查后拒绝 `pipeline_run(L1_build_graph)`；即使 Experts 计划允许该工具也不能提升为图谱管理员。
- `tools/pipeline_run.py` 的执行入口再次拒绝 L1，发生在任务创建和领域调用之前，防止旧/直接 adapter 绕过 gate。返回既有 `PermissionError`，由当前 gated wrapper 投影为不可重试的 `permission_denied`，不新增错误协议。
- 工具描述和参数说明不再向模型宣称 L1 可调用。
- 保留 L0/L2/L3/L4 的当前 owner 绑定与原有业务门禁；不修改可信后台 `L1BuildGraphPipeline`、GraphStorage 或已确认个人图投影发布器。

L1 `student_skills` 仍写旧的共享 `Skill` / `Person`，不是独立 `PersonalGraphSnapshot` / `PersonalSkill` 发布器，因此不能仅放行这个分支来替代已确认资料发布。此决定落实 ADR 0004 与 `personal-graph-owner-scope.md` 的既有分区边界，不新增 Agent 或审批框架。

## 开源参考与适配边界

- OpenCode 本地 commit `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT），`opencode/specs/v2/tools.md` 的 Invocation Context / Built-In Tools：可信调用上下文与模型输入分开；工具须对具体操作/资源提出权限请求；相同工具类型不等于相同权限。
- Codex 本地 commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0），`D:/AGeneral Workspace/_agent_reference/codex/codex-rs/core/src/tools/orchestrator.rs`：执行前审批/权限决策，拒绝不等于可重试执行失败。
- 项目复用现成 LangChain adapter、PreToolUse、GateContext、PermissionVerdict、gated wrapper 及既有私有图发布器；仅加业务操作级拒绝。未复制 TS/Rust 会话内核，也未把 student_id 重写当作共享资源写入授权。

## 复验

仓库根目录运行：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:CPA_TEST_LIVE_PUBLIC_GRAPH = '1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_agent_pipeline_graph_write_gate.py
```

未设置 `CPA_TEST_LIVE_PUBLIC_GRAPH=1` 时，只有真实 Neo4j 哨兵用例跳过，其余检查包括 Agent/Experts 两种模式、两种 L1 分支、直接 tool adapter、L0/L2/L3/L4 正向身份绑定、可信领域 L1 runner 仍可执行。真实库启用时应全部通过。

本轮实际结果：专项 **11 passed**（含真实 Neo4j）；联合 `test_p0_scope_truth.py`、`test_agent_runtime_isolation.py`、`test_personal_graph_scope.py`、`test_personal_graph_publication.py`、`test_experts_plan_gate.py` 回归 **95 passed / 2 skipped**。两个跳过项是已有的个人图实库用例，另需 `CPA_TEST_LIVE_PERSONAL_GRAPH=1`，不属于本次哨兵用例。`git diff --check` 在两个修改的源文件上通过。

## 残留边界

本切片只关闭 Agent 可写 dispatcher 的不安全能力，不意味着所有历史图写接口已完成审计。`GraphStorage` 与可信 L1 仍保留旧共享标签语义；管理任务执行入口、公共数据发布及历史数据迁移需要各自独立授权与验收，不能因此将旧数据认领为公共或个人可信证据。
