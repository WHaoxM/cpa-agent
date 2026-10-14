# CPA-Agent Documentation Map

This directory contains active engineering docs, historical notes, generated diagrams, and competition-writing material. Not every file here is current source of truth.

## Source-Of-Truth Order

Use this order when documents disagree:

1. `../CONTEXT.md` — domain terms, accepted boundaries, phase decisions.
2. `adr/` — durable architecture decisions.
3. This file — documentation organization and freshness policy.
4. Focused guides, runbooks, specs, and architecture docs under the active folders below.
5. Historical logs, generated diagrams, and archived drafts.

## Active Documentation Layers

| Layer | Directory | Purpose | Update trigger |
|---|---|---|---|
| Context | `../CONTEXT.md` | Domain language and accepted decisions | Term, boundary, or phase changes |
| Decisions | `adr/` | Durable architecture decisions | Hard-to-reverse trade-off decisions |
| Architecture | `architecture/` | Current system structure and boundaries | Topology, ownership, or integration changes |
| Specs | `specs/` | Planned changes before implementation | Before coding a non-trivial change |
| Runbooks | `runbooks/` | Operate, diagnose, recover | Operational procedure changes |
| Guides | `guides/` | Maintainer implementation guidance | Repeated maintenance workflow |
| API Notes | `api/` | Internal API contract notes | Endpoint contract or integration changes |
| Frontend (cpa-xingtu) | `frontend/cpa-xingtu/` | Route-level FE design contracts, fixtures/adapter conventions | cpa-xingtu page or ideal API contract changes |
| Archive | `archive/YYYY-MM/` | Stale or superseded docs | Quarterly or after major rewrites |

Folders may be created lazily when the first current document of that type is needed.

## Current Files To Treat Carefully

These files are useful but should not be treated as fully current without verification:

- `dev_log.md`, `bug_log.md` — chronological notes, useful for archaeology.
- `database_design.md`, `arch_v2_db_schema.md` — architecture drafts; the frozen source of truth for DB schemas now lives in `.trae/rules/{mysql,neo4j,milvus}-contract.md`.
- `jd_cleaner_usage.md` — tool usage note; verify against current `cleaning_data/` code before relying on it.

Historical competition material, generated diagrams, arch_v2 layer specs, and personal deliverables have been moved to `archive/2026-07/` and `../archive/personal-deliverables/`.

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
- `specs/self-managed-account-auth.md` — 用户确认的平台自建账号窄闭环、Redis 会话/CSRF/管理员开户与未完成边界
- `specs/admin-source-staging.md` — 管理员 dataset/KB 私有原文暂存、摘要回读、删除与迁移；不等于权威岗位入库或建图
- `guides/documentation-governance.md`
- `guides/windows-powershell-encoding.md` — Windows/PS 5.1 下乱码是解码假象，文件均为 UTF-8；读写姿势与真损坏判定
- `guides/attachment-completeness.md` — 上传附件到 Ask/Agent 的完整性标记、OCR 运行边界和回归步骤
- `guides/agent-http-acceptance.md` — 真实 socket HTTP 的 Agent 多轮/附件旅程、替身边界与失败后回放；不等同于真实模型质量验收
- `reports/2026-10-10-http-agent-acceptance-review.md` — PDF 架构能力与 HTTP/test_client/未验证证据分层，含 SSE 连接红灯定位及验收缺口
- `reports/2026-10-13-secondary-backend-acceptance.md` — 次要入口与 Docker 恢复后真库续验、已复现窄域修复、全量回归及尚未闭合的产品链路
- `guides/career-role-inference-source-coverage.md` — 职业方向推断的完整、空结果、部分结果与依赖来源覆盖合同
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
- `guides/agent-session-turn-coordination.md` — Supervisor 同会话回合的 Redis 跨 worker 租约、开发降级与 fail-closed 部署边界
- `guides/agent-turn-idempotency.md` — 显式 `Idempotency-Key` 的 MySQL 准入、JSON/SSE 终态重放、unknown/过期墓碑及真实跨进程 HTTP 验收
- `guides/agent-prompt-first-draft.md` — OpenCode/Codex 与 Trace 协作判断转译到 Agent 路由、planner、QA 的“先给初稿”契约和本轮状态回放
- `guides/agent-plan-persistence.md` — planner 正文落库、成果索引同事务提交、owner 读取与学习页交接回放
- `guides/plan-handoff-context.md` — Experts 后续步骤保留本轮有界目标摘要；含真实 socket HTTP 交接与伪造业务结果阻断验收
- `architecture/agent-upstream-adaptation.md` — OpenCode/Codex 快照、许可证、参考模块与 Python 薄适配边界
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
- `specs/runnable-demo-hardening.md`
- `specs/pending-code-quality.md`
- `specs/frontend-api-contract-matrix.md` — 真库冒烟快照（非 cpa-xingtu 产品契约权威）
- `frontend/cpa-xingtu/README.md` — cpa-xingtu 全路由设计文档入口
- `runbooks/local-smoke-test.md`
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
- Check `DEPLOY.md` against `docker-compose.yml` and `.env.example`.

Before release or public demo:

- Run backend and frontend smoke checks.
- Confirm README setup path still works.
- Confirm public claims do not imply frontend-backend integration if frontend remains mock-based.
