# 简历确认金路径切片：L0/L2 权威来源与缺证据停机

维护对象：后端与前端工程师。状态：2026-10-09 已实现替身链路与部分真实 MySQL 契约测试；真实简历→模型→浏览器完整链路尚未验收。

## 本次修复的事实问题

旧 L0 使用的 `L0ResumeParser` 没有 `entities` 字段，却将缺失字段解释为 `{}` 并覆盖 `student_profile`，随后记录完成。新链路复用现有 `L0FullParser`、`NSLEPipeline` 与 `PipelineRunner`，不再把文本提取成功等同于可用画像。

当前实现：

```text
页面上传 / Agent pipeline_run(L0)
  → 同一个 L0ParseResumePipeline
  → L0FullParser → NSLEPipeline
  → pipeline_task.result_json 保存结构化候选和页码证据
  → needs_input（结构合格） / partial（结构不完整）
  → 用户显式 POST /api/profile/student/confirm
  → student_profile 写入 + pipeline_task 确认状态写入 + 两者回读
  → 页面 / Agent 共用 L2GeneratePortraitPipeline
  → 语义事实与四维数值均具证据时写入 student_portrait_4d、回读权威 id 后 completed
  → 否则仅保存 partial 管线任务，不写入零分画像
```

L3/L4 已有受控岗位发布仓库和持久化薄适配；**当前目录为空**，实际用户仍停在 `partial`。即使有发布，缺少已确认 D3 语义事实时也不写匹配分数；有完整证据的旧规则分数仍标注 `policy_estimate/not_validated`，报告不包含凭空生成的建议。详见 [L3/L4 来源阻断边界](match-report-source-gate.md)。

## 开源复用与适配边界

- `opencode` commit `388406238bd5ca15564a762840a2362c3a45bd9c`，`packages/opencode/src/tool/read.ts`：参考保留来源窗口与显式截断/展示元数据的边界。本切片不移植 TypeScript 文件工具，不新造文件解析器。
- `openai/codex` commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`，`codex-rs/core/src/tools/approvals.rs`：参考服务端独立的 approval action/context；模型参数不是用户确认。本项目只是职业资料的业务确认适配，不照搬 shell 权限策略。
- 本次没有新增通用 Agent 编排器、任务队列或检查点框架。候选确认使用已有 MySQL 业务记录；它不等于 LangGraph `interrupt`/checkpoint 恢复。后续长任务恢复应接已有持久队列与 LangGraph 持久化，不扩展这里为另一套运行时。

## API 契约

### 1. 候选

`POST /api/profile/student/upload`，multipart `file`，可选 `session_id`。身份只取服务端 Principal。

返回 `success=true,data`：

- `task_id`：已写入的 `pipeline_task` ID，不额外生成占位 ID。
- `status=needs_input|partial`；`profile_ready=false`。
- `candidate_profile`、`structure_validation`。
- `source_evidence`：`task_id`、可选 `file_id`、`blocks`（`block_id/text/page/block_type`）、`page_count`、`observed_pages`、`missing_pages`、`engine`。
- `page` 从 1 开始。覆盖有缺页为 `partial`，否则仍为 `unverified`；`accuracy_verified=false`。文本块存在不证明 OCR 逐字准确或 DOCX 页面完整。
- `next_action=confirm_resume|repair_resume_structure`。

Agent 调用同一 L0 类，其工具结果和任务行都保留 `needs_input/partial`，不再被工具外层改为 completed。待确认状态的 `completed_at` 为 null。

跨页面交接只传任务引用：`GET /api/profile/student/candidate/<task_id>` 按 Principal 回读同一候选。成功字段与上传候选一致；不存在或非 owner 返回 404，非 L0 或已确认等非候选状态返回 409。工具卡片 action 为 `confirm_resume`，resource 为 `pipeline_task/<task_id>`。上传和候选回读均不返回服务端绝对文件路径。

### 2. 确认

`POST /api/profile/student/confirm`：

```json
{"task_id":"task_...","confirmed":true,"acknowledge_partial":false}
```

服务端检查 task owner、L0 类型、候选状态和结构合格。缺页时必须额外显式 `acknowledge_partial=true`；这只允许用户选择使用已识别内容，**不会把覆盖率改为完整**。接口不接受请求中的 profile 或学生 ID 替换候选。

成功返回 `status=completed,profile_ready=true,portrait_ready=false,resource_type=student_profile,resource_id=<Principal student_id>,task_id,next_action=generate_portrait`。表示资料确认完成，不表示画像生成完成。

重复确认当前候选幂等返回；旧候选已被后续确认替代时拒绝。真实 MySQL 已改用 SQLAlchemy 单事务及 owner/profile、task 行锁；profile、确认任务和状态事件一起提交或回滚，并通过真库并发和写后失败测试。事件与恢复边界见 [Pipeline 事件日志](pipeline-event-journal.md)；这仍不等于 LangGraph interrupt 或进程重启续跑。

### 3. 画像

`POST /api/profile/student/pipeline`：

```json
{"pipeline":"L2_generate_portrait","job_id":"可选目标岗位"}
```

不接收客户端 `student_id/skills/profile/confirmed`。L2 使用已确认 profile 的技能和事实；通过 `student_profile` 中的确认来源、task owner、task `confirmed=true`、候选内容一致性核验，单独伪造 `_resume_confirmation` 不可绕过。

L2 仅在 D1–D4 与竞争力分数均有限且语义证据充足时返回 `task_id,status=completed,portrait_id,resource_type=student_portrait_4d,resource_id,source_confirmation_task_id,portrait`。`portrait_id/resource_id` 来自写入后的数据库回读，不是随机占位 ID。若事实缺失或任一分数未知，返回 `status=partial,reason=semantic_evidence_incomplete,next_action=confirm_semantic_facts,portrait_id=null`，只保留任务，不写入零分画像。薄入口还回读任务 owner/status。

L3/L4 在未满足来源或评分条件时为 `501,status=partial`，`data.reason=confirmed_portrait_unavailable|catalog_publication_unavailable|scoring_evidence_incomplete|verified_match_unavailable`，并返回实际 `pipeline_task` 引用及 `next_action`；不要在页面播放模拟后续阶段。L2 新生成的画像附带 `source_portrait_task_id`，旧画像缺少此信息时需要重新生成，不能自动视为可信。

## 验证与未完成边界

运行：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_resume_golden_path.py -q
```

测试覆盖候选不覆盖资料、Agent 状态一致、未确认下游禁止、跨 owner 拒绝、确认后权威回读、内容篡改拒绝、缺页确认、真实 Flask 上传/确认/L2 路由、失败不覆盖、L3/L4 在前置证据不足时不生成业务成果。解析器、模型和 MySQL 采用测试替身，不能以这些测试代替真实样本与容器联调。

剩余限制：

1. 上传 L0 仍同步执行，尚未将该业务 pipeline 接入媒体持久队列；不得宣称 HTTP 中断或进程重启可恢复。
2. NSLE 已取消静默首段截断，超窗分块及合并仍需真实简历/模型逐字段验收；Agent/Experts 全链 token 预算尚未闭合。
3. DOCX 页码由解析块提供，非 Word 排版页码保证；复杂形状/图片文字与逐字 OCR 准确率未验收。
4. L2 无目标岗位时的重复写入仍受 MySQL nullable unique key 行为影响，需真库测试去重策略；本次以明确回读 id 保证不返回虚构资源。
5. L3 发布仓库和 match 回读、L4 证据快照、页面→会话任务引用已有窄切片；真实岗位目录、职业建议质量与推荐来源尚缺，不能宣称完整金路径。
6. 严格模式下既有未确认资料将被下游拒绝，需经新候选确认流程，不应批量把历史资料标成已确认。
