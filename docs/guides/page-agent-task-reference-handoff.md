# 页面与主 Agent 的权威任务引用交接

## 复用边界

- 参考 `opencode/` 当前快照 `388406238b` 的 `packages/opencode/src/session/prompt.ts`：用户消息由有类型的 parts 组成，文件/资源引用不靠往 prompt 粘贴浏览器内容。此处只复用数据流思想；OpenCode 的 TypeScript session runtime 不直接移植到 Flask。
- 本项目沿用现有 `content_parts → prepare_user_turn → ModeRuntime`、MySQL `pipeline_task` 和 cursor 事件协议，没有新增通用编排器或事件总线。`AG-UI/CopilotKit` 尚未在本仓库 Vue 3 + Flask 运行时建立身份透传、恢复或任务结果等价性证据，不让协议选型先于权威事件链。

## 交接契约

1. 职途页面上传/确认/L2 后记录真实 `task_id`，通过 `GET /api/pipeline/events/<task_id>?cursor=N` 按 Principal 回放 MySQL 已提交状态；`pending/running` 才继续轮询，不以秒数估计百分比。历史任务未安装 journal 时事件可以为空，`task` snapshot 仍是权威状态。
2. 页面进入 AI 助手仅携 `task_id` URL 参数。助手先用事件 API 作展示预检；发送时提交 `{type:'artifact_ref',task_id}` content part，不提交页面摘要、标题、分数或确认标志。未点击发送不会自动运行模型。
3. 服务端在 STM 写入**之前**按 Principal + task ID 读 `pipeline_task`，拒绝跨 owner、伪 ID、未知类型/状态及结果 ID 不一致。L0 completed 还校验当前档案确认版本；L2 completed 校验 `student_portrait_4d` 同 owner/id/JSON。L3/L4 即使任务行 completed，也只挂载任务引用，不把未经此适配器核验的业务 ID 宣称为已验证成果。
4. 模型只得到任务类型、状态、结果 JSON 的 SHA-256 版本、已核验资源 ID（如适用）及 `next_action`；这**不是**简历全文、职业报告全文或真实事件流本体。原文件须走 Document Access。部分状态保持部分状态，不默默补全。
5. Agent→职途页保留 `?task_id=` 入口；若不是待确认 L0 候选，页面仅回放任务 snapshot，不伪造画像展示。

2026-10-10 扩展：非 Pipeline 成果不再伪装成 `task_id`。学习计划等通过 `resource_ref(uri)` 交接；服务端在 STM 写入前按 Principal 调用统一只读资源目录，核验当前记录并挂载版本/状态元数据。具体 URI、分页、状态和跨 owner 拒绝见 `docs/guides/agent-business-resources.md`。原 `artifact_ref(task_id)` 合同保持不变。

## 验证与剩余限制

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_pipeline_reference_handoff.py backend/tests/test_turn_outcome_status.py
cd cpa-xingtu
npm run build-only
npm run type-check
```

负向用例覆盖跨 owner、伪任务 ID、L2 缺业务行/JSON 不一致、浏览器伪造 summary 被丢弃。`type-check` 目前被 `roles.ts`、情绪轮盘、`useCareerInsights.ts`、`LandingView.vue` 的既有类型错误阻断；本链路文件没有新增类型错误。

仍需在运行中的 Docker 上以真实 Principal 验证浏览器整条请求链、后端 journal migration、回放与页面刷新。AG-UI、checkpoint/interrupt 恢复、L3/L4 业务成果读取及完整职业报告在这条薄适配里未实现。
