# CPA-Agent Documentation Map

This directory contains active engineering docs, historical notes, generated diagrams, and competition-writing material. Not every file here is current source of truth.

**Start with [Current Work](current-work.md)** for a short, dated view of active workstreams and where to read next. It is a navigation snapshot, not a replacement for live runtime evidence or the source-of-truth order below.

For the dated Q1/Q3/Q4 acceptance split, see the [next-stage acceptance audit](reports/2026-10-17-next-stage-acceptance-audit.md); its incomplete gates are not release approval.
The latest local Q1 hash-locked image build and cold-restore replay have a [focused evidence report](reports/2026-10-17-q1-hash-image-offline-replay.md); the separate offline Dockerfile and wheelhouse are local artifacts, not a published release.

For the current student journey from a report-based action plan to recorded practice and assistant reflection, use the existing [Agent plan persistence guide](guides/agent-plan-persistence.md) and its [2026-10-21 replay evidence](reports/2026-10-21-student-practice-evidence-reflection.md). The report is a dated replay, not a new parallel guide.

## Source-Of-Truth Order

Use this order when documents disagree:

1. `../CONTEXT.md` — domain terms, accepted boundaries, phase decisions.
2. `adr/` — durable architecture decisions.
3. This file — documentation organization and freshness policy.
4. Focused guides, runbooks, specs, and architecture docs under the active folders below.
5. Historical logs, generated diagrams, and archived drafts.

For fresh-clone reproduction, start with [../README.md](../README.md) (scope/runtime) and [../DEPLOY.md](../DEPLOY.md) (ordered commands and checks). The frontend is a separate repository whose own README/DEPLOY govern Mock/Pages and local HTTP modes. An older smoke snapshot or green test count does not supersede current runtime behavior.

## Active Documentation Layers

| Layer | Directory | Purpose | Update trigger |
|---|---|---|---|
| Context | `../CONTEXT.md` | Domain language and accepted decisions | Term, boundary, or phase changes |
| Decisions | `adr/` | Durable architecture decisions | Hard-to-reverse trade-off decisions |
| Architecture | `architecture/` | Current system structure and boundaries | Topology, ownership, or integration changes |
| Specs | `specs/` | Planned changes before implementation | Before coding a non-trivial change |
| Runbooks | `runbooks/` | Operate, diagnose, recover | Operational procedure changes |
| Guides | `guides/` | Maintainer implementation guidance | Repeated maintenance workflow |
| Reports | `reports/` | Dated validation evidence and remaining gaps, not evergreen runtime truth | After a focused audit or replay |
| API Notes | `api/` | Internal API contract notes | Endpoint contract or integration changes |
| Frontend (cpa-xingtu) | `frontend/cpa-xingtu/` | Route-level FE design contracts, fixtures/adapter conventions | cpa-xingtu page or ideal API contract changes |
| Archive | `archive/YYYY-MM/` | Stale or superseded docs | Quarterly or after major rewrites |

Folders may be created lazily when the first current document of that type is needed.

## Historical and Pending Material

- [Code Wiki](archive/2026-10/CODE_WIKI.md) — generated in 2026-06; its default Agent, Python, route and deployment descriptions are superseded by current code and focused docs.
- [Development log](archive/2026-10/dev_log.md) and [bug log](archive/2026-10/bug_log.md) — early chronological notes, useful for archaeology but not current task state.
- [Relational database design](archive/2026-10/database_design.md) and [multi-store schema draft](archive/2026-10/arch_v2_db_schema.md) — historical drafts. Current MySQL structure is verified from Alembic migrations, live schema and [the migration runbook](runbooks/mysql-migrations.md); do not rely on ignored local `.trae/` rules as a distributable source of truth.
- `jd_cleaner_usage.md` — a ReAct-oriented legacy usage note. `JdCleanerTool` and `cleaning_data/p1_LLM.py` still exist, so it is retained for the explicit legacy path; the default `/api/agent/chat` route enters the supervisor instead. Recheck the actual route and inputs before following this note.

Historical competition material, generated diagrams, arch_v2 layer specs, and personal deliverables have been moved to `archive/2026-07/` and `../archive/personal-deliverables/`. Both are local-only ignored archives, not guaranteed to exist in a fresh clone. The five `archive/2026-10/` files linked above belong with this navigation change; keep their old-path removals and new archived copies together in review or replay.

Early-stage root directories with no remaining value have been archived under `../archive/`:

- `../archive/third-party/MiroFish/` — third-party swarm-intelligence project (only referenced as a design-pattern inspiration in comments and historical docs; no code dependency).
- `../archive/early-prototypes/mvp-entity-extractor/` — early NS-LE pipeline prototype, superseded by `backend/app/pipeline/entity_extractor/`.
- `../archive/early-prototypes/data-samples/` — early JD/resume sample datasets and benchmark JSON outputs, not referenced by current code.
- `../archive/early-prototypes/knowledge_base-sample/` — early `knowledge_base/job_posting.json` sample; `backend/scripts/milvus/generate_vectors.py` notes that the source directory must be rebuilt before running.

## Recommended Active Set

For open-source runnable hardening, keep these active and maintained:

- `../AGENTS.md`
- `../CONTEXT.md`
- `README.md`
- `adr/0001-config-and-workspace-layers.md`
- `adr/0002-pattern-upgrade-retrieve-parse.md`
- `adr/0003-request-scope-runtime-isolation.md` — Principal、会话 owner 与 strict/demo 身份隔离边界
- `adr/0004-graph-as-main-agent-capability.md` — 用户确认图谱接同一主 Agent；公共图谱证据工具与用户图谱隔离要求
- `adr/0005-shared-evidence-and-interaction-policy.md` — Ask/Agent/Experts 共用权威证据层，以检索上下文与执行权限区分交互模式；实施及真实验收仍需逐项核对
- `adr/0006-agent-turn-idempotency-ledger.md` — Agent 回合的持久幂等账本、跨进程重放与状态边界
- `adr/0007-account-session-epoch-revocation.md` — 自建账号停用/降权/改密后的全会话代数撤销，显式迁移与 fail-closed 回退边界
- `adr/0008-versioned-mysql-schema-migrations.md` — MySQL 结构由 Alembic 版本账本统一接管；冻结旧初始化快照，后续只新增版本
- `guides/open-source-hardening.md`
- `guides/account-profile-and-notification-preferences.md` — authenticated account facts, revisioned profile writes, persisted notification intent, and unavailable security/delivery capabilities
- `specs/self-managed-account-auth.md` — 用户确认的平台自建账号窄闭环、Redis 会话/CSRF/管理员开户与未完成边界
- `specs/admin-source-staging.md` — 管理员 dataset/KB 私有原文暂存、摘要回读、删除与迁移；不等于权威岗位入库或建图
- `specs/admin-dataset-publication-binding.md` — 私有 dataset 单条记录经服务端来源重读、摘要绑定进入正式管理员人工岗位发布；不等于自动或官方来源认证
- `guides/documentation-governance.md`
- `guides/windows-powershell-encoding.md` — Windows/PS 5.1 下乱码是解码假象，文件均为 UTF-8；读写姿势与真损坏判定
- `guides/attachment-completeness.md` — 上传附件到 Ask/Agent 的完整性标记、OCR 运行边界和回归步骤
- `guides/agent-http-acceptance.md` — 真实 socket HTTP 的 Agent 多轮/附件旅程、替身边界与失败后回放；不等同于真实模型质量验收
- `guides/backend-user-journey-acceptance.md` — 学生业务旅程的场景 owner；含简历工作台 HTTP/MySQL/Celery 回归，以及报告追问的真实前端登录、页面操作、HTTP/权威状态验收入口
- `reports/2026-10-21-student-action-plan-followup-replay.md` — 从报告差距到一周行动计划的真实 Vue 登录回放、进度持久化、切换账号隔离、来源过期历史保留与未覆盖范围
- `reports/2026-10-21-student-practice-evidence-reflection.md` — 学习中心逐步记录练习成果、附件已读/未就绪时回原对话复盘、重登恢复与来源过期的真实前端回放
- `reports/2026-10-21-frontend-report-followup-evidence-fix-replay.md` — 报告追问自动组合已确认简历、正文/依据分离、真实浏览器与 HTTP/MySQL/Redis 回放及未覆盖范围
- `reports/2026-10-10-http-agent-acceptance-review.md` — PDF 架构能力与 HTTP/test_client/未验证证据分层，含 SSE 连接红灯定位及验收缺口
- `reports/2026-10-13-secondary-backend-acceptance.md` — 次要入口与 Docker 恢复后真库续验、已复现窄域修复、全量回归及尚未闭合的产品链路
- `guides/career-role-inference-source-coverage.md` — 职业方向推断的完整、空结果、部分结果与依赖来源覆盖合同
- `guides/career-market-seed-boundary.md` — 职业冷启动市场门禁、允许估算的版本化公共参考链、三库投影与真实HTTP/页面证据
- `guides/legacy-exams-publication-gate.md` — 旧技能自评入口只推荐当前已审核发布的岗位，回读时复核撤回与版本
- `guides/pipeline-dispatch-boundary.md` — L3 专用持久队列/恢复与其他旧 Pipeline 启动入口的 503 边界
- `guides/docx-ocr-and-human-cer.md` — DOCX 嵌图真实 Sidecar OCR 与扫描简历逐页人工 CER 审阅包；未签审不产生准确率
- `guides/legacy-doc-resume.md` — 旧版二进制 DOC 的可选 Apache Tika 文本抽取、来源未核验标记与配置缺失恢复
- `guides/semantic-skill-extraction.md` — L1 技能抽取去除词典/关键词伪回退后的结构化模型候选、原文证据门禁及残余语义硬编码清单
- `guides/resume-entity-linking.md` — L0 简历技能 mention 原文门禁、已发布公共技能有界全文召回与模型 equivalent/related/unresolved 提议；不是跨语言向量召回或自动确认归一化
- `guides/skill-vector-index.md` — 从真 MySQL 技能分类结构幂等重建公共 Milvus 向量、来源标识、失败重放与本机 BGE-M3 验收边界
- `guides/resume-diagnostic-report.md` — 与可信岗位专项 L4 分离的已确认简历诊断报告，事实章节、逐页引用、模型解读质量标记和真实回读验收
- `guides/agent-entity-review-feedback.md` — 人工审阅 revision 如何经主 Agent 资源与 EvidencePack 实时读取，及跨页版本变化拒绝
- `guides/agent-business-resources.md` — 主 Agent 的跨功能只读 URI 目录：已确认简历/画像、匹配/报告、学习计划及个人记录；含 owner、状态、分页、开源复用与未覆盖边界
- `guides/ask-evidence-continuation.md` — Ask 共享 EvidencePack 的 Redis 会话游标、跨轮版本校验、过期/来源失败恢复及真实 HTTP 验收
- `guides/ask-grounding-experiment.md` — Ask 单章节计数的权威直答、真实 HTTP 对照，以及语义双审默认禁用/复杂分析未验收边界
- `guides/domain-semantic-evidence.md` — 简历结构化、D1/D3/D4 事实证据与未知分数停机边界
- `guides/portrait-behavior-assessment.md` — D1 客观学校类型与评分层级分离、D3 带来源的候选行为评价及确认门禁；缺证据仍保持 partial
- `guides/semantic-routing-hardcode-audit.md` — Ask/Agent 语义路由移除关键词/正则快捷判定后的模型结构化决策及未验收项
- `guides/experts-structured-evidence.md` — Experts 意图/计划去语义关键词捷径，以本轮工具引用及 owner+业务行回读判成果
- `guides/agent-plan-skill-slice.md` — Agent 动态白名单计划、按需 Skill 加载、回复过程隔离与持久 run/取消/接续/显式专家包尚未完成的边界
- `guides/school-fact-source-slice.md` — 教育部历史 211 正向名单的独立版本/哈希、owner 已确认资料按需读取和未知/D1 隔离边界；真实 MySQL/模型/页面及跨 run 固定尚未验收
- `guides/agent-run-persistence.md` — owner-bound Agent run MySQL 状态与 Celery outbox/claim/replay、HTTP submit/status/events/cancel API、版本化输入快照和 QA-only 持久执行器；真实链路与工具/Skill边界按证据区分
- `reports/2026-10-18-agent-run-live-replay.md` — 一次合成只读任务的隔离 HTTP→MySQL/Celery→模型回放、缺资料语义诊断、重复投递/先取消安全边界，以及本次未覆盖的 Skill/工具/页面范围
- `reports/2026-10-17-agent-reply-differential-baseline.md` — 合成简历下 clean/history 两次真实 Agent HTTP 的 Lead、子 Agent、公开回复和 Vue 源码级页面投影；不把单样本差异归因于上下文污染
- `reports/2026-10-18-agent-history-context-continuity.md` — Agent/Experts Lead 接入最近四条有界、不可信历史的实现与真实 HTTP 差分；不把一次模型输出差异解释为因果或污染
- `runbooks/public-skill-partition.md` — 审核种子 `PublicSkill` 的发布、可用性核验与安全降级；公共图与个人图继续隔离
- `guides/personal-graph-owner-scope.md` — 当前已确认资料到私有技能快照的 owner/source 分区发布、幂等回读、撤回与主 Agent 固定模板；旧混存 Person/Skill 不认领
- `guides/reviewed-entity-graph-projection.md` — 人工审阅的 equivalent 实体在独立 revision、Celery 重放与 Neo4j 事务下投影到 owner-private 个人图；related/reject/defer 不提升为公共真值
- `guides/durable-media-jobs.md` — Celery/Redis 媒体队列、worker 启停、重启恢复验证与 MinerU Sidecar 的真实边界
- `guides/pipeline-event-journal.md` — Pipeline MySQL 事务事件游标、确认原子性及 LangGraph checkpoint 的独立恢复试验
- `guides/durable-resume-confirmation.md` — 生产 L0 候选确认的 LangGraph MySQL interrupt/resume、跨进程幂等与边界
- `guides/p0-identity-and-result-truth.md` — 主 Agent 与部分用户 API 的 Principal 归属、真实结果 ID 和失败状态收束边界
- `guides/historical-favorites-tool-state.md` — 历史收藏权威 ID/删除状态与工具公开状态结构化契约
- `guides/resume-confirmation-golden-slice.md` — L0 候选/逐页证据→用户确认→L2 有证据画像；事实不足不落零分成果
- `guides/resume-agent-team-trace.md` — L0 异步四职责 Agent 交接、真实检查点百分比、owner-scoped 轨迹与统一预览运行/恢复边界
- `guides/match-report-source-gate.md` — L3/L4 发布仓库、匹配/报告持久化快照与来源/质量不满足时的 partial 门禁；当前真实岗位目录为空
- `guides/match-report-request-contract.md` — `/api/match/run` 与 `/api/report/generate` 的 JSON 对象及 job ID 输入边界，确保非法请求在任务/报告写入前以 400 拒绝
- `guides/job-catalog-model-review.md` — 官方岗位源的真实模型逐字段审查、引文与评分门禁、独立 MySQL 审计；blocked 审查不自动发布
- `specs/source-only-job-catalog.md` — 来源已核对但当前不可评分岗位的独立公开层、管理员精确确认、L3/L4 禁止及已有 MySQL volume 迁移
- `specs/hybrid-public-candidate-boundary.md` — Hybrid 召回岗位只读当前已验证发布快照、职业方向与岗位分层、向量元数据不得绕过公开门禁
- `specs/jd-vector-write-provenance.md` — JD 向量写入仅来自当前人工发布快照，版本与摘要绑定；历史索引迁移仍待单独处理
- `guides/page-agent-task-reference-handoff.md` — 功能页与主 Agent 的服务端 task 引用交接，拒绝客户端自报成果
- `guides/agent-call-budget-ledger.md` — LangGraph 调用前窗口预检与 Agent/Experts request-local provider usage 账本的已接边界和未覆盖路径
- `guides/agent-observability-privacy.md` — Langfuse 默认只导出调用账本元数据；含简历/回复的追踪内容须部署方显式 opt-in
- [当前系统架构图](architecture/current-system-overview.svg) — 以现行代码/配置接线为准的一页式总览；[Mermaid 源文件](architecture/current-system-overview.mmd) 可编辑。实线为已接线，虚线为可选/配置条件，灰色 Mock 不连后端。
  - 绘图基线：主仓 HEAD `590f055b5307f3704afab32df754aa28e14326e1`（dirty）；前端 HEAD `da7a3fa81cdf28f2ba1ab1d1396a37944e28ea0d`（dirty）。任务恢复仅覆盖已持久化任务子集，不代表 L0–L4 全链路保证。
- [AI 工作台运行细图说明](architecture/agent-runtime-diagrams.md) — [上下文流图（SVG）](architecture/agent-context-flow.svg) / [Mermaid 源文件](architecture/agent-context-flow.mmd)，以及[回合状态机（SVG）](architecture/agent-turn-state-machine.svg) / [Mermaid 源文件](architecture/agent-turn-state-machine.mmd)。
- `guides/agent-session-turn-coordination.md` — Supervisor 同会话回合的 Redis 跨 worker 租约、开发降级与 fail-closed 部署边界
- `guides/agent-turn-idempotency.md` — 显式 `Idempotency-Key` 的 MySQL 准入、JSON/SSE 终态重放、unknown/过期墓碑及真实跨进程 HTTP 验收
- `guides/agent-prompt-first-draft.md` — OpenCode/Codex 与 Trace 协作判断转译到 Agent 路由、planner、QA 的“先给初稿”契约和本轮状态回放
- `guides/agent-plan-persistence.md` — 传统 planner 正文兼容、报告差距到一周行动计划的来源绑定/步骤进度/owner 持久化约定与学习页回放入口
- `guides/plan-handoff-context.md` — Experts 后续步骤保留本轮有界目标摘要；含真实 socket HTTP 交接与伪造业务结果阻断验收
- `architecture/agent-upstream-adaptation.md` — OpenCode/Codex 快照、许可证、参考模块与 Python 薄适配边界
- `reports/2026-10-19-education-screening-structured-v2.md` — 教育条件 Skill 驱动的结构化 QA/finalizer opt-in 候选、release/usage pin、无模型回归、一次 Qwen A–E 样本未通过体验门槛及 HTTP 未验收边界
- `reports/2026-10-19-education-screening-structured-v5-no-model.md` — v5 ID-only finalizer selector、服务端自然答复渲染、A–E/注入/非法选择/版本漂移 no-model 契约与 HTTP/模型未验收边界
- `architecture/platform-agent-capability-architecture.md` — 平台级 AI Native：会话与功能页共用能力运行契约、模块实况、开源选型和迁移金路径
- `reports/2026-10-09-resume-page-extraction-audit.md` — 本地两份简历的脱敏逐页提取核验；覆盖证据与 OCR 准确率边界
- `reports/2026-10-09-agent-document-context-evaluation.md` — 多附件、提示词路由、模型窗口与职业问题的契约验证及未验收范围
- `reports/2026-10-09-agent-runtime-architecture.md` — 实际 Agent 主链、迭代上限修复与平行未接线模块
- `reports/2026-10-09-graph-agent-runtime-audit.md` — 图谱工具运行链、Skill/JobPortrait 公共读降级及残余数据作用域风险
- `reports/2026-10-09-agent-user-interaction-audit.md` — 引用、进度、澄清与失败恢复的公开交互契约审计
- `reports/2026-10-09-platform-acceptance-baseline.md` — DOCX、OCR CER、全链预算及 Ask 事实/来源/跨轮连续性的专项验收基线与未通过项
- `reports/2026-10-10-general-agent-guide-architecture-audit.md` — 用户通用 Agent 指南的逐页架构映射、三模型交叉核验、本轮 JSON/SSE 输入边界改造与后续缺口
- `reports/2026-10-10-agent-guide-core-decisions.md` — 指南 14 页的必须做/值得做/已有/不适用取舍、两个 worktree 的选择性接收与拒绝、规划 HTTP 实测及请求幂等实施合同
- `specs/ai-native-agent-platform.md` — 主 Agent + 受控领域能力的目标数据流、开源选型与阶段验收
- `specs/student-workbench-experience.md` — 2026-10-14 已确认的学生私人工作台产品目标、画像/诊断/Agent 过程分层和并行任务交接；目标不等于当前运行能力
- `specs/student-workbench-state-contract.md` — 学生工作台任务/成果/版本/逐条过程的后端调查与 P0–P3 迁移任务；P0 只读投影、P2 后台分析及 P1 新画像历史快照首切片已实施，旧数据安全迁移、P3 与完整故障验收待实施
- `specs/school-context-skill.md` — 院校背景上下文认知 Skill 的主动解读、事实/社会印象/评分分层与接入验收；未上线
- `specs/runnable-demo-hardening.md`
- `specs/pending-code-quality.md`
- `specs/frontend-api-contract-matrix.md` — 真库冒烟快照（非 cpa-xingtu 产品契约权威）
- `frontend/cpa-xingtu/README.md` — cpa-xingtu 全路由设计文档入口
- `runbooks/local-smoke-test.md` — historical startup smoke commands; its older frontend HTTP/demo examples must be reconciled with `../DEPLOY.md` before use
- `runbooks/mysql-migrations.md` — MySQL Alembic 版本迁移的备份、升级、核验和旧库接管；旧单次 SQL 与初始化快照不再作为增量变更入口

## Archive Policy

Archive instead of deleting when a document still has historical value.

Use:

```text
docs/archive/YYYY-MM/<original-name>.md
```

Add a short header to archived Markdown files:

```markdown
> Archived on YYYY-MM-DD.
> Superseded by: <path or reason>
```

Do not archive accepted ADRs. Supersede them with a later ADR if needed.

## Maintenance Cadence

Weekly during active hardening:

- Update `AGENTS.md` if agent operating rules changed.
- Update `CONTEXT.md` if domain boundaries or phase decisions changed.
- Mark stale docs in this map before moving files.

Monthly:

- Move superseded generated diagrams and old notes into `docs/archive/YYYY-MM/`.
- Check `README.md` claims against runnable code.
- Check `../DEPLOY.md` against both Compose overlays, `.env.example`, current Alembic head, and the frontend mode defaults.

Before release or public demo:

- Run backend and frontend smoke checks.
- Confirm README setup path still works.
- Confirm public claims do not imply frontend-backend integration if frontend remains mock-based.
