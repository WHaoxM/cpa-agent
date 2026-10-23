# Agent 学习计划落库与功能页读取

## 根因与边界

此前 planner 的 `plan.current` 写入被关闭，`verify_plan_updates` 只核验画像/匹配/报告，行动计划没有权威正文表，HTTP 模式学习页也拒绝读取产物。仅把纯文字回复标记为 `completed` 会掩盖这个断点。

原对话计划保存只允许已绑定 `student_id` 与 `session_id` 的 Agent/Experts planner 回合；下方方向探索另经本人明确业务操作生成，不由聊天擅自保存。模型只写自然语言正文；服务端清洗正文后将 `agent_action_plan` 与 `agent_artifact` 在一个 MySQL 事务中提交并回读。过短、超过 8000 字的正文不静默截断落库；对“无法制定计划、请补充信息”这类明确索要前置输入的回复也拒绝保存。`plan.current` 仅保存 plan/artifact id 与摘要，不保存正文；对话公开的 `artifact_ref` 从服务端提交结果产生，不接受模型自报 id。领域记录的 `draft` 表示建议尚待用户调整，artifact 的 `completed` 只表示本次**保存动作已完成**，不代表招聘结论经过事实核验。

`GET /api/agent/artifacts/<artifact_id>` 先按 Principal 查询索引，再按同 owner 与原 session 读取 `agent_action_plan.body_text` 并校验 SHA-256。前端 `continue_plan` 在 HTTP 模式把 artifact/session id 交给学习中心；学习中心按同一接口加载完整正文，刷新后仍可读取。Mock 模式保留原来的演示计划组件。

## 报告缺口到行动计划（当前新增业务约定）

从报告详情进入助手后，只有本轮 EvidencePack 同时读到本人当前已确认简历和有效岗位报告、版本绑定仍一致、读取覆盖完整，并且答复指出具体差距，才显示“把这个差距变成行动计划”。浏览器只提交短期 opaque handoff id 和用户的时间答复；报告 URI、报告/简历版本与差距摘要留在 owner-bound session context。每次进入计划阶段及保存前，服务端重读报告/简历来源；来源变化则阻止新计划，要求学生回到当前报告重新开始。

行动计划生成需要 3–5 个结构化步骤；模型输出不是持久化凭据。`agent_action_plan`、`agent_action_plan_step` 和 `agent_artifact` 在同一 MySQL 事务中提交，并按 `(student_id, handoff_id)` 做重复消费约束。结构化步骤的 `status` 由 `completed` 数量计算为 `not_started` / `in_progress` / `completed`；artifact 的 `completed` 仍只代表计划记录已保存。步骤通过 `PATCH /api/agent/action-plans/<plan_id>/steps/<step_id>` 设置完成状态，请求体严格只有 `{ "completed": boolean }`，owner 来自登录 Principal；它是设置而非切换，因此重复发送同一状态幂等。计划详情只读；学生不能改写步骤正文。

计划保存后的报告或简历版本若撤回/过期，详情保留历史步骤和已勾选状态，并只返回友好的 `source.status=expired`；不再把它显示为当前来源。旧自由文本计划仍返回 `content.body`，但没有结构化步骤时不推造复选框或完成数。

## 学生记录练习成果并回到原对话复盘

每个结构化步骤最多保存一份可更新的当前练习记录：`agent_action_plan_step_evidence` 以 `step_id` 为主键，保存学生自述、可选 `attachment_file_id` 与时间；不复制附件正文。该表通过 plan/step 外键随计划清理。`PUT /api/agent/action-plans/<plan_id>/steps/<step_id>/evidence` 只允许 `summary`（1–2000 字）和可选 `attachment_id`；登录 Principal 决定 owner，服务端还要求附件 owner 与计划 owner 相同、附件 session 与计划来源 session 相同。省略 attachment_id 表示保留现有附件；显式传 `null` 可解除关联。重复提交更新同一行，不改步骤正文或勾选进度。计划详情投影只返回文件名和解析/读取状态，不向浏览器暴露 file_id。

记录的来源一律标记 `student_submitted`、`verified=false`；勾选完成仍只是行动进度，二者都不更新简历、画像、匹配分或报告。原对话带回 owner-scoped `career://me/action-plans/<plan_id>` 后，EvidencePack 由服务端给该资源加入 `evidence` 区段，读取步骤状态、学生文字、来源报告/简历当前状态，以及同 session 附件读取状态。只有文件 owner/session 已核验且文件已就绪时才通过既有 `session_resources.mount_resources` 与 `document_access.materialize_for_ask` 读取文本；读取结果覆盖完整标为 `read`，有界/来源不完整标为 `partial`，排队、失败、无文本或读取错误不提供附件正文。任何情况下，模型都必须将练习记录表述为学生提交的自述，不作为已核验简历经历或能力证明。计划来源过期/不可用时，这些资料只可用于历史复盘，不可用于当前求职判断。

对话中的资料读取状态使用结构化 `evidence_summary` 部件展示，不拼进 `reply` 正文；行动计划来源显示友好名称，旁注明确练习记录未经能力或经历核验。历史消息沿用同一部件回读，避免离开后重新进入对话时退化为内部来源说明。

行动计划与其附件分别计读：行动计划的步骤/自述已读，不代表附件正文已读。存在附件时，依据部件另列“练习附件”；附件处理中、失败、无文本或读取不可用时，附件标为暂不可用且整体依据不显示为完整。详见[2026-10-21 真实前端回放](../reports/2026-10-21-student-practice-evidence-reflection.md)。

这条数据路径复用既有 URI 资源目录、显式 coverage/partial 状态和附件 owner/session 读取边界：OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/tool/read.ts` 有界读取/截断约定，以及 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 URI resource list/read 思路；不复制其执行内核、不新增通用记录框架，也不绕过现有 `document_access`。上游参考和适配范围登记见[Agent 上游复用记录](../architecture/agent-upstream-adaptation.md)。

MySQL schema 变更由新增 Alembic `0012_action_plan_step_evidence` 管理；运行时兼容旧应用的建表守卫不代替生产迁移。旧自由文本计划及没有记录的历史步骤继续可读，不补造结构化成果或附件。

## 职业方向尝试来源（2026-10-22）

### 原页内陪伴与方案确认

`career_exploration_analysis_turn` 经 `0018_exploration_analysis` 追加，关联已有探索、run、顺序、学生消息、来源签名和结构化候选结果。run、outbox、回合绑定在同一事务中提交；同一幂等键输入变化返回冲突，未确定结束的任务不自动另起一份。`career-exploration` 仅使用已绑定探索资料的只读 QA 和既有工具循环，不加入业务写角色；模型不能指定 owner，也不能自己提交最终决定。

步骤生成的结果只是暂存，页面仅在 run completed 后读取。复盘的权威 `review_json` 在 run 成功完成的同一事务中发布；用量、来源或任务核对失败不能留下可供本人决定使用的伪成功复盘。勾选与学生提交成果仍分开，附件沿用 owner/session 与真实内容读取边界。

确认方案向既有 `POST /api/career/explorations/<id>/rounds` 提交 `proposal_id`、`expected_revision` 和本人选择。服务端核验最新方案、来源、方向、问题及时间，在同一事务创建轮次与原样步骤并关联确认记录。问题或时间变化须先重新分析；重复确认回读相同计划，不能改写已确认理由。旧版没有 proposal_id 的明确选择入口及已有计划继续兼容；新页面不再调用旧的独立生成按钮。

行动计划新增 `source.kind=career_exploration`，由本人探索轮次关联，不伪造 report URI、报告版本或已完成 L2。原 `create_from_steps` 的岗位报告来源校验保持不变；新 `create_exploration_steps` 仅作薄来源适配，之后复用同一计划／步骤／artifact 事务、进度和练习记录读写。`0016_career_exploration` 新增探索、不可变选择轮次及 nullable `agent_action_plan.exploration_round_id` 唯一关联；旧报告计划和纯文字计划继续读取。

生成前核对 owner、session、本人确认资料和目录版本；保存前再次核对并锁定轮次。唯一 round 绑定使响应丢失后重试不会新增计划。未开始的轮次仅可经本人显式重新准备关闭；迟到模型结果不能给关闭轮次保存计划。已产生计划的轮次必须保留实践与决定，不通过关闭绕过复盘。

事务中复用现有 `ConfirmationTransaction` 只读适配核对来源及既有计划，不持锁后再次从连接池借连接；已完成迁移／准备后，绑定事务的 ActionPlanStore 读回显式跳过运行时 DDL。该边界来自两连接池双并发的实际失败，不通过调大池或忽略异常掩盖。

复盘读取现有计划资源的 `evidence` 区段及实际就绪附件；计划标题和学生勾选只作背景，不能推导已经执行了安排。服务端把“我实际提交的记录”直接投影为本人文字和感受原文，模型只提供单独标为待核对的解读、未知及备选方向。练习和附件仍是未认证的学生提交。材料变化后旧复盘显示 stale，不能作为当前决定输入；原资料变化后旧轮次仍可历史复盘，不再作为当前求职判断。

助手资源为 `career://me/explorations/<id>`，指定历史轮次使用 `/rounds/<round>`；服务端派生该轮的精确计划，不能用最新轮次替换。继续沿用 EvidencePack 的覆盖、续读与友好 `evidence_summary`，不向正文显示 URI。模型预算沿用 LLMClient，超过本业务读取范围或生成被截断明确拒绝，不静默保存部分结果。学生最终决定只由 owner-scoped PUT 提交，不由助手回复触发。端到端证据由[职业分析页面契约](../frontend/cpa-xingtu/pages/career-analysis.md)链接维护。

## 开源对照与适配

沿用 `docs/architecture/agent-upstream-adaptation.md` 记录的 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（`packages/opencode/src/session/prompt.ts` 的 typed parts）与 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（显式状态/结果边界）的做法，仅复用“正文留在权威存储、对话传引用、状态由服务端核验”的契约。Python/Flask + MySQL 的 owner、事务与学习页读取仍是本项目的薄适配；不移植上游会话内核，也不把模型 JSON 当业务提交凭据。

## 回放

1. 用 `mkg` 运行 `python -B -m pytest backend/tests/test_action_plan_persistence.py backend/tests/test_action_plan_handoff.py backend/tests/test_agent_action_plan_handoff.py -q`。验证结构步骤、状态派生、重复写不重复成果、失败/非法模型结构不保存，以及 handoff 来源只能来自当前 owner session。
2. 对已有数据库应用 Alembic head `0012_action_plan_step_evidence`；旧计划记录保留，新增逐步成果表为空。不要用运行时临时建表替代迁移验证。
3. 真浏览器入口继续复用 `backend/tests/manual/frontend_report_followup_browser_probe.py`：从报告提问、生成计划、学习中心勾选/记录/修改成果并上传合成 txt、回原对话复盘、切账号、原学生重登回读同一进度/成果。该回放用真实 Vue 页面/HTTP、登录 Cookie/CSRF、Flask、MySQL、Redis 和既有 AgentFileService 文本入库/文档读取；只把 Ask 与计划步骤生成器替换为确定性模型，不验收建议/模型/OCR 质量。设置 `CPA_FRONTEND_REPLAY_REDIS_URL` 为专用且预先为空的 Redis DB（默认 DB 12），并满足 mkg、Node/Vite、Chrome 和 MySQL 运行条件。依赖缺失不得记为通过。

2026-10-10 历史记录：合成 owner 的早期自由文本计划验证了正文持久化、owner 读取和学习中心只读正文；该次页面尚无持久进度。当前结构化计划与逐步进度以本节新契约及日期化回放报告为准，不能把旧证据当成本轮浏览器验收。

联调时曾出现同一个请求有时 `partial`、有时 `completed`：检查到 Windows 上两个 `-B run.py` Flask 进程同时监听 `5001`，旧进程仍执行关闭 planner 写入的代码。停止旧进程并确认唯一 listener 后，同样的真实 HTTP 请求稳定产生 artifact。遇到“代码和单元测试已更新，但页面仍无产物”时先核对端口监听进程及启动时间，避免把旧进程响应误判为新代码缺陷。
