# 学生工作台后端状态与过程契约：现状、迁移和验收

**状态：**P0 只读投影、P2 确认后后台处理、P1 新画像快照首切片已实施；旧数据安全迁移、P3 与 P2 全面故障验收待实施，目标契约不等于当前 API 承诺。
**产品依据：**[学生私人工作台体验任务](student-workbench-experience.md)，2026-10-14。
**调查口径：**下列现状表原先依据 2026-10-14 的源码调查；随后已复核注册路由、当前 worker/Beat 和真实请求，实施记录见文末。工作树含并行未提交修改，P1–P3 实施前仍须重新核对运行事实。

## 1. 当前可核对的运行契约与断点

| 事项 | 当前证据 | 与目标的差距 |
|---|---|---|
| L0 异步导入 | `POST /api/profile/student/upload?async=1` 在 MySQL 同事务写 `pipeline_task`、`pipeline_resume_dispatch`、接收事件，返回 202；Celery `cpa.pipeline` 的 claim token、租约和 Beat 恢复扫描覆盖 L0；`GET /api/pipeline/trace/<task_id>` 按 owner、cursor 回放 L0 轨迹。见 `backend/app/api/profile.py`、`backend/app/services/pipeline_resume_jobs.py`。 | “上传已接收/四检查点完成”不等于一轮自动分析（报告和画像）完成。worker/Beat 未运行时保持 pending，不能用页面计时假装推进。 |
| L0 确认 | L0 候选为 `needs_input` 或 `partial`；`POST /api/profile/student/confirm` 由用户确认后，当前 `student_profile._resume_confirmation.task_id` 绑定来源，L0 任务为 `completed`。缺页的确认可承认已读部分，但不补出缺页。 | 现有流程以整体候选确认作为下游前提；“只打断处理关键例外”还不是当前核验策略。LangGraph MySQL checkpoint 只续 L0 确认，不续整个 Agent 回合。 |
| L2 画像 | 保留旧同步 `POST /api/profile/student/pipeline`；新确认事务另写确定性 L2 任务与 `pipeline_portrait_dispatch`，worker/Beat 独立接续。生成器任一维度未知时返回 `status=partial`，只在 `pipeline_task.result_json.portrait_candidate` 保存候选，`portrait_id=null`；五个数值齐全才写 `student_portrait_4d` 并标 `completed`。该旧表分数字段 `NOT NULL DEFAULT 0`。见 `backend/app/services/pipeline.py`、`pipeline_portrait_jobs.py`。 | “已处理资料且明确留下未知”仍不能成为有稳定成果 ID 的当前画像。不能把 `partial` 直接改名 `completed`，也不能往旧非空分数字段写零/虚构总分；L3/L4 当前依赖可评分完成画像，必须继续单独把关。 |
| 画像回读 | `GET /api/profile/student/candidate/<L0 task>` 可回读当前 L2 partial 候选；`GET /api/profile/student/<id>/4d` 只读已落 `student_portrait_4d` 的数值成果，核对当前简历、用户自述和学校审核来源；旧来源变化返回 409。`candidate` 对已被后续确认替代的 L0 也返回 409。 | 没有统一的“当前画像（允许未知维度）+ 上一版本快照”的稳定读面；旧结果只报 stale，不能按产品目标以“基于上一版资料”只读展示。当前无目标岗位查询按 `competitiveness DESC` 取首行，也不适合承担版本选择。 |
| 简历诊断 | 确认事务写确定性 `R1_resume_diagnostic` 与 `resume_diagnostic_dispatch`，worker/Beat 独立处理；旧 `POST /api/report/resume-diagnostic` 仍可显式同步触发，`GET` 读当前，旧来源 `409 stale`。无缺页、模型解释和方向章节满足引用校验时 `completed` 仅代表简历范围内报告形成；预算/模型/引用失败为 `partial`。见 `backend/app/services/resume_diagnostic_jobs.py`、`resume_diagnostic_report.py`。 | 旧版报告不能只读回看；后台失败/预算与重试策略尚未完成全矩阵验收。 |
| 任务事件 / Agent 过程 | `/api/pipeline/events/<task_id>` 是 MySQL 触发器记录的状态变更，非工具执行明细；L0 `/trace` 是阶段、交接、模型名、用量等白名单元数据，不存简历正文、实际输入输出。聊天 `tool_egress.py` 把工具原始结果变为脱敏摘要卡；`thinking_trace.py` 可合成第一人称 UI 文字，不是原始执行事件。 | 三套来源不可互充。学生要逐条、可展开的**真实**调用记录，目前无跨 L0/L2/报告/聊天的持久、owner-scoped、安全输入输出公开契约；不能把合成“思考”或 4 个检查点冒充原始工具事件。 |
| 预算、截断与恢复 | 模型回合有 preflight/usage ledger，报告把 `input_budget_exceeded`、`model_output_truncated`、引用失败置 partial；通用 Agent 回合 `Idempotency-Key` 是 MySQL 准入/已完成 envelope 重放，`unknown` 不自动重跑；后台 `fork_jobs` 仍非持久执行。L2/诊断首切片已具持久意图、claim/租约与 Beat 重派。见 `docs/guides/agent-call-budget-ledger.md`、`agent-turn-idempotency.md`。 | 截断/用量未覆盖全部异步阶段，worker 丢失与所有失败重试未完成实测；聊天发“继续”不构成后端可恢复分析，也不可推论到任意工具回合。 |

上述是源码和已有验收资料的**调查结论**，不是本轮真实服务验收。实施前先用目标环境复核数据库迁移、worker/Beat、路由注册与每个状态返回；尤其不能让旧的同步或 demo 入口充当异步工作台事实。

## 2. 目标状态：任务、成果、事实覆盖各有一层

不要继续以一个 `status` 或一个百分比承载三种含义。对外读模型建议（字段名待实现时校准）：

| 层 | 含义与来源 | 对外语义 |
|---|---|---|
| `run` / 工作流 | 这轮分析的持久执行、attempt、任务子步骤和恢复游标；不能凭前端推测。 | `queued/running/waiting_for_user/settled/failed` 等执行语义。`settled` 只表示本轮自动步骤已结算，**不**保证两份成果齐备；失败/预算耗尽须有阶段、可恢复性与下一步。L0 的四检查点仅作为 L0 子步骤进度。 |
| `resume_diagnostic` 成果 | 已确认简历版本及报告行、质量与缺失章节。 | `ready/partial/not_started/failed/stale`；`ready` 限定“简历诊断”，不能暗示岗位专项报告完成。 |
| `portrait` 成果 | 有稳定 ID 的当前画像快照、D1–D4 各自 `score|null`、依据、未知原因和来源版本。 | `ready_with_unknowns` 仍是**当前结果**，不是失败或等待四维补齐；另可有 `ready_scored`、`not_started/failed/stale`。如果存在必须解决的关键矛盾则明确 `waiting_for_user`，普通未知不阻断形成结果。无目标岗位时不自动推导“综合求职竞争力”。 |
| `scoring_eligibility` / 后续门禁 | 旧 L3/L4 的确定性评分要求，和上述可阅读画像不同。 | 只在可评分画像 + 已发布、可评分岗位等旧权威条件成立时为可用；未知分数画像可以支持**探索方向**，不能突破正式匹配/报告门禁。 |

迁移时应保留现有 `pipeline_task.status` 的后端兼容含义，另建组合读投影或版本化成果契约，而不是对原字段作无迁移的语义重命名。每个成果状态来自**权威行/哈希/来源校验**；一个子步骤 100% 或 HTTP 200 不能推断全局 `ready`。

## 3. 版本、owner 与自动执行的边界

1. 单次导入得到 owner-bound `run_id` 与 L0 `task_id`。后续当前确认版本以 `source_confirmation_task_id` 为锚；用户自述修订、学校评分审核版本、源文件覆盖/页块摘要也要纳入相应成果的版本指纹。版本指纹由服务端持久数据计算，不接受前端自报身份、简历正文或旧成果 ID 来提升“当前”。
2. 新简历保存后自动排队**新版本**分析；新结果在权威事务提交前，旧报告/画像只能显示“基于上一版资料”，可查看但不写成当前分数。旧版本只读查看需保留同 owner 的历史行并带来源时间/版本，不应沿用当前 `GET /report/<id>`、`GET /candidate/<id>` 的 409 语义冒充已经实现历史回看。新版本部分失败时，不静默回退为旧版“当前”。
3. 当前报告与当前画像各自独立切换；某个成果生成失败不应撤销另一个已验证成果。聊天引用必须读同一权威版本，不能写独立“聊天画像”。
4. 一次导入后“自动继续”由已有 MySQL 持久意图 + Celery `cpa.pipeline` 派发/重派承接 L0；确认后 L2 与简历诊断已接入同一模式，旧显式同步入口仍在。at-least-once、claim token、幂等成果提交、失败重派和预算停机仍须逐步实测，不得把进程内线程、Celery result backend 或前端保持打开当权威任务状态。
5. 用户确认/重要例外仅通过现有 owner + 版本检查写回。普通无法判断记为 `unknown`，不强迫学生逐条批准，也不让自述自动变简历原文。需要用户回答且尚未回答的阻塞项与已明确可保留的未知项分别建模。

## 4. 逐条过程事件：能看到真实调用，但不是私有思考泄露

目标不是把 `thinking_trace.py` 的合成文本扩展成“原始过程”。任务事件最少包含稳定 `event_id`、`run_id/task_id`、owner、attempt、父子调用 ID、实际发生时间、actor、技术名称、动作标题键、`started/completed/partial/failed`、实际调用参数/结果的**可公开结构化投影**、截断/遮蔽说明和可核验的关联成果/来源 ID。每一次真实调用独立入账，不把十次 `normalize_entity` 折为一次。游标顺序、跨页 attempt 归属、重试历史与当前轮次必须可回放。前端可翻译标题，但不得创造未执行的调用。

“展开原始记录”表示该次执行所留下的真实事件及允许公开的字段原值，**不是另一段模型写的摘要**；它也不承诺把未经筛选的 prompt、完整简历/数据库行或思考草稿向浏览器倾倒。后端在采集点制定按工具/Skill 的输入输出字段 allowlist，并给每个字段 `shown/redacted/omitted/truncated` 及原因；电话、邮箱、密钥、系统提示、其他 owner 数据与私有 reasoning 在写入和公开边界均须检查。即使输出过长或有遮蔽，也保留该条真实事件、完成状态和可检查的非敏感字段；若能安全分页访问较大结果，需 owner-bound continuation 和原始来源引用，而不是用“成功”摘要冒充全文。不要将通用 `tool_egress.py` 的 160 字符摘要卡直接认作已满足详情契约。

范围要显式：L0 的现有 `/trace` 先保持白名单元数据；L2、报告和聊天各自是否产生可持久读取的事件，以新采集/适配层实测为准。任务列表/过程栏只读，切换到哪个任务就读其 owner-bound 事件；聊天消息和过程事件共用任务/成果引用，但不是一张未验证的全平台事件总线。

## 5. 开源参考与适配决定（改 Agent 基础能力前复核）

已核对本机快照：OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`（MIT，`opencode/`）和 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`（Apache-2.0，`D:/AGeneral Workspace/_agent_reference/codex/`）。

| 参考模块 | 可借鉴的不变量 | 不可直接平移之处与本项目薄适配 |
|---|---|---|
| OpenCode `packages/opencode/src/session/{processor,message-v2}.ts`、`tool/truncate.ts` | 每次工具调用独立 `pending/running/completed/error`，保留调用 ID、输入/输出与起止时间；截断显式标记并保留继续读取线索。 | TypeScript 会话存储不是本项目 owner + MySQL/Celery 业务事务；借鉴事件字段与截断语义，在现有任务执行点写入可安全公开投影。不可复制其完整未经脱敏工具输出。 |
| OpenCode `packages/opencode/src/session/compaction.ts`、`processor.ts` | 使用实际 provider usage 与窗口/重试状态而非前端时间猜测。 | 现有 `LLMClient`/`budget.py` 是接入点；不另造 token 计数器。OCR、报告和 Agent 各阶段需标明账本覆盖缺口。 |
| Codex `codex-rs/core/src/{rollout.rs,rollout_budget.rs}`、`protocol/src/turn_input.rs` | 执行记录与预算/转交状态分开，admission/调用结束不能替代最终成果提交。 | Rust thread/rollout 不是可直接嵌入 Flask 的恢复内核；不从其事件名推导本项目职业成果完成。 |
| 本项目已安装的 Celery 5.x + Redis、LangGraph MySQL checkpoint 3.0.0 | L0/L3 已用持久意图、队列投递和 MySQL 领域真值；L0 确认已有 `interrupt/resume` 专用切片。 | 先扩用现有派发/事务围栏，明确各业务任务的恢复范围；不要新建平行通用队列，也不要宣称 checkpoint 能自动续跑任意 Agent 工具。 |

若实施时选择自建完整事件仓或调度器，必须先记录现成方案为何不能在当前 Python/Flask、owner-scoped、隐私与领域事务约束下适配，并以失败注入/真请求证明；此文不批准造一套新的通用 Agent 内核。

## 6. 优先级、最小首切片与验收

**P0 — 先定权威读面，不改评分。** 盘点本机当前注册路由/迁移状态；在同一 Principal 下把当前 L0、简历报告、L2 partial/完成成果与来源版本组成只读工作台状态。清楚返回 `run` 与两种成果的独立状态、未知维度、`next_action` 和旧版标志；仍保持旧 L3/L4 门禁。最小首切片建议用一个虚构学生：完成 L0 确认、生成 `completed` 简历诊断和 `partial` L2 候选，回读时绝不显示“整轮画像已完成”或缺证据的 0 分。此切片先验证旧记录能否稳定做“当前”，不声称已经完成自动排队或历史回看。

**P1 — 画像版本化成果迁移。** 设计允许 `null` 维度的独立、不可变画像快照及当前指针/版本选择（或证明另一种等价结构），保留旧可评分表/读面供 L3 使用；迁移有证据的旧行而不把历史默认零当真值。未知维度可形成当前画像，关键矛盾仍阻塞，已完成任务与成果状态分开。新增后核对无目标岗位、多次执行和并发资料修改，不覆盖旧版记录。

**P2 — 自动后台编排。** 在确认/保存新版资料的原子边界创建后续持久执行意图；复用 `cpa.pipeline` 的 claim、重派和幂等成果提交。L2、报告分开结算；模型预算耗尽、未知 usage、截断、断电/重启各有明确状态与可重试位置，不以“回复继续”恢复。先只移一个后续阶段到后台，证明后再接第二个。

**P3 — 真实过程事件与隐私。** 按实际执行点逐条持久化，先选择一个确有多次调用的 L0/报告环节，做 owner、cursor、attempt、字段级遮蔽、截断/续读和跨进程回放；随后才扩到其他任务类型、聊天引用与前端常驻栏。不得用合成思考文本、预制进度或摘要卡代替实测。

每个切片的**真请求验收**至少覆盖：新账号身份上传 → 关闭/离开页面 → worker/Beat 接续 → owner-bound 任务和逐条事件回读 → L0 确认 → 两个成果分别生成/回读 → 更新简历后旧/新版本不串 → 缺证据维度为 `null`、综合分不造 → 另一账号 404/拒绝且没有跨 owner 写入。故障注入覆盖 L2/报告写后失败、worker 重启、模型输出截断/预算不足、broker 不可用；核对 MySQL 的任务、报告、画像及版本指针，而不以 200、前端截图或单测替身单独宣称完成。实际执行需注明 MySQL/Redis/模型/OCR/Sidecar 哪些为真实服务，哪些为替身；测试学生与文件使用虚构资料并清理专属记录。

## 7. 2026-10-14 P0 首切片实施与仍缺的边界

- 已注册 `GET /api/profile/student/workbench`。它只从当前 Principal 和 MySQL 读取最近 L0 任务、当前确认版本、同源 L2 任务以及独立的简历诊断行。对外分别给 `run`、`portrait`、`resume_diagnostic`，不是以 L0 四个检查点推断两种成果。返回 `based_on_previous_upload` 表明有较新的未确认导入时，现有成果仍基于上次确认版本；不把该旧成果标成当前新导入的评分。
- 持久化 L2 `partial` 且来源、用户自述修订和学校审核均有效时，读模型映射为 `portrait.state=ready_with_unknowns`、`scoring_eligible=false`、四维 `score|null`、缺失依据、原 L2 `task_id`。它**没有**新的 `portrait_id`，原 `pipeline_task.status=partial`、`student_portrait_4d` 与 L3/L4 门禁原样保留。完整 L2 只有权威画像表回读通过且四维分数有效时才给 `ready_scored`；来源不一致拒绝或标 stale，不回退旧任务冒充当前。
- 真实默认 worker/Beat 已在本机运行。本轮先用隔离 Flask 5002/Vite 5175，随后重启默认 Flask 5001 并用正式 Vite 5174 再跑一遍；两次都借助同一真实 `cpa.pipeline` worker/Beat、MySQL、Redis、Neo4j、真实结构模型和 PyMuPDF，对虚构 PDF 通过登录 Cookie 走 `upload?async=1 → needs_input → 浏览器确认 → L2 partial → 简历报告 completed → workbench GET`。L0 有 17 个真实阶段、1/1 页文字、6 段证据；GET 返回画像 `ready_with_unknowns`、报告 `ready`，画像表没有这名学生的虚构零分。桌面与 390px 浏览器实际渲染、无横向溢出，最终默认地址截图见 `output/career-nav-browser-check/l0-text-pdf-confirmed.png` 和 `l0-text-pdf-confirmed-mobile.png`。测试夹具账号、任务、文件和报告已清理。
- 后端 HTTP/MySQL 回归 `backend/tests/test_student_workbench_http_mysql.py` 覆盖确认前、当前部分画像、无诊断报告、较新导入时的旧来源标记、另一 Principal 看不到原 owner 的结果。该测试 L0 候选是受控夹具、没有验证 OCR；与上一条真实 PDF 浏览器链路互补。
- 前端正式页面是 `cpa-xingtu`。P0 时首屏改为“已知／未知／下一步”的文字摘要，移除真实画像页的三张内部 KPI 卡和无目标岗位的通用“综合竞争力”；细分维度仍保留可核对的服务端分数或空值，不更改 mock 演示数据。后续 P2 首切片已移除确认后的前端自动同步 L2/报告调用；详见下一节。旧版历史只读、独立画像快照 ID、真实逐条工具输入输出与完整故障注入仍待 P1–P3。此首切片不能称整个学生工作台已经交付。

## 8. 2026-10-15 P2 确认后自动后台分析首切片

- **基础能力复用记录：**改动前检查 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/session/message-v2.ts`、`tool/truncate.ts`，以及 Codex `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的 `codex-rs/core/src/rollout.rs`；其单次工具执行/截断记录属于 P3 借鉴，不直接移植为 Python/Celery 调度。P2 实际复用本仓 `pipeline_resume_jobs.py`、`pipeline_match_jobs.py` 与已装 Celery/MySQL 的事务意图、任务 ID、claim/租约和恢复扫描，薄适配只增加画像/诊断的业务版本围栏与成果提交，不造第二套通用任务引擎。
- `confirm_resume_candidate` 在确认档案的同一 MySQL 事务写入两份确定性任务 ID 与 dispatch 行；提交后分别向 `cpa.pipeline` 投递。`save_self_report` 仅为新增自述修订创建新版 L2 意图，不重做简历范围诊断。任务只传 ID，不把简历正文推入 Redis。画像任务同时绑定 owner、L0 确认版本、自述修订与 worker claim；诊断任务绑定 owner、L0 确认版本及 claim，报告写入前再次核对。
- `GET /api/profile/student/workbench` 的 `analysis_run` 汇总后台状态，`portrait`、`resume_diagnostic` 各自可为 `processing/ready_with_unknowns/ready/partial/failed/stale`。`run` 仍只代表 L0。页面只轮询读面和 GET 诊断报告，显示“后台分析中，可离开”；不再由浏览器在确认后另发 L2/报告 POST。L2 `partial` 是可读的未知画像，不是“没有运行”；报告 `partial` 不阻断画像。
- 新结构是 Alembic `0006_student_analysis_dispatch`；冻结的 `init_mysql.sql` 未修改。迁移在维护窗口执行，已有显式安装表被核对/接管。迁移真实 MySQL 临时库覆盖空表和旧显式表两种升级、重复运行，为 **4 passed**；工作台真实 HTTP+MySQL 用例对两个任务做 broker 失败、恢复扫描重派、后台执行、重复消费和跨 owner 校验为 **1 passed**。此用例的 L0 候选、broker 故障与恢复扫描均为受控替身，不等于 Beat 进程中断验收。
- 默认 Flask 5001/Vite 5174、真实 MySQL/Redis/worker/Beat、真实模型与文字 PDF 的 `--pdf` 探针在确认后**不打开页面也不发 L2/报告 POST**，两份权威成果仍分别为画像 `ready_with_unknowns`、诊断 `ready`，dispatch attempt 各为 1。`--pdf --browser` 还从正式页面确认并在桌面/390px 回读同一两份结果，无横向溢出。首次浏览器回放发现 L0 事件流切换误停了分析刷新，修正独立定时器后重放通过；截图同上。扫描件 OCR、worker 意外终止、模型预算/截断与完整历史版本仍未由此覆盖。

## 9. 2026-10-15 P1 新画像版本快照首切片

- **原问题：**L2 `partial` 虽可在工作台阅读，却只有任务 ID、`portrait_id=null`；同一学生重新生成或确认新简历后，旧结果无法作为独立成果只读回看。把旧可评分表的非空/默认零字段直接用作历史会把未知伪装成零分。
- **当前实现：**Alembic `0007_student_portrait_snapshot` 增加按源 L2 任务唯一的快照表。L2 部分/完整结果与任务终态在同一事务写入；完整结果还与旧可评分行同事务提交。稳定 `snapshot_id`、owner、确认任务、自述修订、目标岗位、完整画像 JSON 和 SHA-256 内容摘要一起保留；只追加不覆盖旧快照。工作台对新快照校验 owner、任务状态、哈希和任务结果后返回 `snapshot_id`，未知维度仍为 `null`。当前版本依据现有确认版本、最新 L2 和学校审核门禁选出；旧可评分表和 L3/L4 不借历史快照放宽条件。
- **历史读面：**`GET /api/profile/student/portrait-snapshots` 列最近 20 份当前账号的无目标岗位画像，`GET /.../<snapshot_id>` 回读指定版本。新确认后旧快照仍可按 owner 读取，但 `is_current=false`、`scoring_eligible=false`；篡改内容摘要或任务结果返回 409，跨账号返回 404。正式 `cpa-xingtu` 页面提供可展开的只读版本列表，不把旧版替换为当前画像。已有旧 L2 没有新快照时仍按旧路径读当前，明确 `snapshot_id=null`，**不**伪造迁移成功。
- **真实验收：**`CPA_TEST_PIPELINE_MYSQL=1` 的工作台 HTTP/MySQL 用例经两次 L2、第二份简历确认与新版 L2 验证 3 份快照、旧版只读、跨账号隔离及哈希篡改拒读，**1 passed**。`CPA_TEST_LIVE_MYSQL=1` 的临时数据库 Alembic 空表/旧显式安装表升级与重复运行 **4 passed**。完整后端套件为 **1142 passed、108 skipped、7 warnings**（未启用 opt-in 真实 MySQL 用例）；默认 Flask 5001/Vite 5174 + 真 worker/Beat/Redis/MySQL/Neo4j + 模型与文字 PDF 的 `--pdf --browser` 再次通过：确认后后台画像 `ready_with_unknowns`、报告 `ready`、新快照与任务 ID/哈希一致，页面展开并查看版本；截图 `output/career-nav-browser-check/l0-text-pdf-history.png`。前端 type-check/build 通过。**未完成：**有证据的旧评分画像迁移、诊断报告历史、并发资料变更与 worker 崩溃的全矩阵测试；扫描 PDF OCR 不由这次文字 PDF 回放证明。
