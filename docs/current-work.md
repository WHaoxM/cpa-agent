# 当前工作入口（2026-10-17 快照）

这是一页**工作状态导航**，不是新的架构权威或完成证明。状态随工作树和运行环境变化；开始任务时先重新核对 `git status`、实际代码与服务。长期边界仍以 [`AGENTS.md`](../AGENTS.md)、[`CONTEXT.md`](../CONTEXT.md)、[ADR](adr/) 为准；运行行为高于文档描述。

## 先按目的读取

| 要做什么 | 最短入口 | 不要误用 |
|---|---|---|
| 在主仓改后端 | [`AGENTS.md`](../AGENTS.md) → [`README.md`](../README.md) 的“运行环境”一节 → 相关 [`CONTEXT.md`](../CONTEXT.md) 段落 | 不从旧报告推断今天的路由和服务状态 |
| 本机启动、部署或恢复 | [`DEPLOY.md`](../DEPLOY.md) → [MySQL 迁移手册](runbooks/mysql-migrations.md)；本机未提交的镜像提案见 `docs/specs/production-image-hardening.md` | 提案、Compose 静态检查和 `/health` 均不是上线批准 |
| 学生工作台与简历链路 | [产品目标](specs/student-workbench-experience.md) → [状态/迁移契约](specs/student-workbench-state-contract.md) → [最近一次后端验收](reports/2026-10-13-secondary-backend-acceptance.md) | 目标契约、旧测试计数不等于当前真实服务已通过 |
| 前端页面与接口 | [前端文档入口](frontend/cpa-xingtu/README.md) → 独立仓库 `cpa-xingtu/README.md` | 主仓文档不代替前端仓库的实际路由；Mock 页面不是真实后端验收 |
| Agent 通用能力改造 | [上游复用记录](architecture/agent-upstream-adaptation.md) → [平台能力架构](architecture/platform-agent-capability-architecture.md) | 本地 `opencode/` 是参考快照，不是本项目运行时依赖 |
| 知识产权材料 | `ip-materials/README.md` | 申请稿、竞赛材料不作为现行代码与权属事实的替代 |

完整文档分层和历史材料入口见[文档地图](README.md)，不必每次通读其“Recommended Active Set”。

旧版根目录 `CODE_WIKI.md` 与四份早期日志/数据库设计稿已保留在 [`archive/2026-10/`](archive/2026-10/)；它们只供追溯，不再与现行运行指南并列。

## 当前工作流与核验点

以下是从 2026-10-17 工作树和文档得到的**盘点**，不是对他人未提交改动的归属判断或优先级授权。

| 工作流 | 观察到的状态 | 下一核验点 |
|---|---|---|
| 生产部署与恢复 | `main` 基线之上仍有未提交部署候选；[隔离卷恢复实证](reports/2026-10-17-q1-isolated-volume-recovery.md)已回读三种数据库和文件标记。[干净候选镜像运行回放](reports/2026-10-17-q1-candidate-runtime-replay.md)第三候选固定本机基础 digest/139 项依赖版本，在又一全新恢复 project 上确认只读根/零 capability、显式迁移、默认 Gunicorn、Beat→worker、本机 HTTPS 正式账号与 owner-bound MySQL 结果。恢复卷顶层 owner 冲突已在**隔离卷**复现并经停机后的精确顶层调整复验；候选 commit/镜像仅留本机。 | 真实旧卷内部 owner 尚未排查；新增 139 项哈希锁**仅为未提交候选**，没有 hash 模式干净安装或新镜像回放；registry、可信域名/证书和完整业务链仍是发布门槛。 |
| 学生工作台 | 最近提交 `a8ef1f1` 涉及持久分析和部署检查；[状态契约](specs/student-workbench-state-contract.md)记录 P0、P2、P1 首切片及旧数据迁移、P3、故障矩阵等缺口。 | 具体任务开始时重新核对路由、迁移、worker/Beat 与 owner-bound 成果，不直接沿用文档中的历史测试数字。 |
| Agent 运行时实验 | 22 个未跟踪模块已在[逐项取舍报告](reports/2026-10-17-q3-agent-candidate-disposition.md)逐文件对照现行实现与本机上游快照；22/22 继续隔离，不删除、不整包接线。此前默认 HTTP 多轮只用确定性 Supervisor/存储替身回放 `1 passed`，不是真模型验收。 | 若出现具体默认路径缺陷，才按上游复用、owner/预算/工具结果和真实请求验收决定薄适配；先确认文件归属才可归档。 |
| 前端 | `cpa-xingtu/` 是独立 Git 仓库，本地主仓不跟踪其源码；[Q4 分流回放](reports/2026-10-17-q4-frontend-worktree-review.md)核对了 13 个未跟踪文件，并在独立前端分支修正一条 Mock 岗位介绍串用前端文案的实际页面缺陷。 | Mock 与后端 HTTP 分开验收；13 个旧稿/截图/脚本仍待归属。该仓本地没有 `AGENTS.md`，不擅自补造规则。 |
| 竞赛与知识产权交付 | 根目录竞赛文档和 `ip-materials/` 属另一条交付线；后者有自己的说明及分件入口。 | 与代码发布分开管理版本、权属与对外承诺；不按“根目录杂物”自动移动。 |

## 待办队列（建议处理顺序，非已获授权的产品优先级）

| 序号 | 状态 | 可独立领取的下一步 | 完成判据 / 当前阻断 |
|---|---|---|---|
| Q0 工作区保护 | 整理范围已核验；后续持续执行 | 每次切换工作流先看主仓及独立前端仓的 Git 状态，按下文分组领取，禁止对未归属内容做 `git clean`、重置或混合提交。 | 五份旧文档的旧路径移除与新归档必须成对看待；整理提交不代表其他工作流已获准入主线。 |
| Q1 部署候选 | 原冷快照已恢复到三个全新目标 project；最新一次在隔离卷调整顶层 owner 后，以只读根/零 capability 通过文件卷可写性、本机 HTTPS 正式账号 owner-bound 窄链和五类 marker 回读；下一候选已有 139 项/2,937 哈希的 lock 与静态回归，**尚未构建/运行** | 按[镜像运行回放](reports/2026-10-17-q1-candidate-runtime-replay.md)先逐项盘点真实旧卷内部 owner，并让下一镜像在受控构建器完成 hash 模式安装与新 digest 回放；待服务器/registry/可信 HTTPS 确定再做外部验收。 | 只调整测试卷顶层并不能保证旧文件/WAL 可写；静态 lock/旧镜像 dry-run、自签 HTTPS、收藏窄链及 Beat 空队列成功不能替代完整业务链或生产发布。 |
| Q2 学生工作台 | 待现场重验 | 选一条有用户结果的路径，从 HTTP 入口到持久状态重放；随后按[状态契约](specs/student-workbench-state-contract.md)逐项领取缺口。 | 真 MySQL/Redis/Neo4j、worker/Beat、归属回读和失败收束均须标清是否实际可用；不继承旧报告测试数字。 |
| Q3 Agent 候选模块 | 22 件已逐项分流，当前均继续隔离；归属未确认 | 按[Q3 报告](reports/2026-10-17-q3-agent-candidate-disposition.md)保护原件；只在出现具体默认路径缺陷时领取单项适配，不以草案清单整包实施。 | 尚无候选接线/真实模型验收；自动写词典、进程内“持久”状态与不完整工具/文件边界禁止原样接入。 |
| Q4 前端独立仓 | 13 件已分流；一条 Mock 缺陷已在独立分支修正 | 按[Q4 报告](reports/2026-10-17-q4-frontend-worktree-review.md)认领旧设计、G6 截图及两个脚本；尤其先定三类缺失岗位介绍的正确数据合同，处理已跟踪包入口指向未跟踪且红灯的检查脚本。 | 前端分支 `codex/q4-role-intro-mock-boundary` 的浏览器回放和构建只验 Mock；不并入主仓，也不宣称 HTTP/正式账号验收。 |
| Q5 竞赛 / 知识产权 | 待权属与版本确认 | 按 `ip-materials/README.md` 分件检查源码快照、作者/协议和对外材料；根目录四件竞赛文件单独定归属。 | 不把校稿 PDF、竞赛描述或未冻结工作树当作可提交事实；不替他人决定申请主体。 |

Q1–Q5 是不同交付单元；本页只提供入口和停机条件，不自动开启部署、重构或对外递交。

[续审报告](reports/2026-10-17-residual-worktree-review.md)记录的是当时的 **11 个已跟踪修改、37 个可见未跟踪文件**及分组意见。2026-10-17 18:24（上海时间）再次核对，主仓为 **11 个已跟踪修改、48 个可见未跟踪文件、暂存区空**；增加了哈希锁候选等新工作和来源未明的小文件，非技术交付及真实简历审计材料仍按本机 exclude 隐藏但保留。独立前端仓仍为 **13 个未跟踪文件**。下方 114 文件表是此前提交前的完整盘点快照，不能与当前数字直接相减推断文件被删除；接手时应重新运行盘点。

## 未跟踪文件分流（主仓，2026-10-17 提交前快照）

以下按 `git -c core.quotePath=false ls-files --others --exclude-standard` 的**实际文件**计数，不按 `git status` 中折叠的目录条目计。提交前盘点可见 **114 个文件，12 组恰好覆盖 114 个**；分类是处理建议，不证明作者或允许提交。整理分支提交后，归档和本页等将不再计入未跟踪数；接手时须重新盘点。

| 范围 | 文件数 | 类别与处理 |
|---|---:|---|
| `docs/archive/2026-10/*` | 5 | 本轮已核对正文的旧文档归档；**保留并成对核验旧路径**，不清理。 |
| `docs/current-work.md` | 1 | 本轮当前入口；保留，后续按日期刷新。 |
| `docs/reports/2026-10-17-*-triage.md` | 2 | 本轮 Agent/部署分流证据；保留为限时报告。 |
| `docs/reports/2026-10-10-*` | 2 | 既有真实 PDF/语义探针证据；保留审计，不提升为现行运行保证。 |
| `backend/production_wsgi.py`、`backend/scripts/ops/*`、四个 `backend/tests/test_{production_wsgi,release_preflight,volume_archive,volume_snapshot}.py`、`docker-compose.prod.host-proxy.yml`、`docs/specs/production-image-hardening.md` | 10 | 部署候选源码、测试、配置及提案；**待确认归属及 Q1 验收**，不与本轮整理混合提交。 |
| `backend/app/services/agent_runtime/{agent_loop_integration,session_compaction,session_input,session_runner,tool_registry}.py`、`backend/app/utils/` 中 17 个未跟踪 `.py` | 22 | Agent 基础能力实验；保持隔离，按 [专项核查](reports/2026-10-17-untracked-agent-modules-triage.md)逐一取舍，不整包接线。 |
| `.opencode/MIGRATION_PLAN.md` | 1 | 旧迁移草案；留作候选思路，不作为执行指令或现行架构。 |
| `.ulpi/design/{DESIGN,landing-revamp}.md` | 2 | 落地页设计稿；待前端归属确认，不搬入后端主线。 |
| `ip-materials/*` | 62 | 内部申请材料、LaTeX 原稿、分件/汇总 PDF 与清单；按其 README 单独管理版本和权属，不整目录递交或当缓存清理。 |
| 根目录 `作品简介.docx`、`作品设计文档.pdf`、`必看-2026移动云杯AI_Coding参赛材料.md`、`附件2：2026移动云杯AI_Coding赛道_参赛作品.docx` | 4 | 竞赛资料；归属和对外版本待确认，保留原位。 |
| `workspace/default/branding/logo.png` | 1 | 暂缓 workspace 样例/素材；保留，不宣称 live branding 功能。 |
| 根目录 `query`、`r.name.includes('jsdelivr')).length` | 2 | 提交前快照中内容分别为 `Docker Desktop Service`、`result: None` 的疑似调试残留；**来源不明，保留待认领**。同日又持续出现相似小文件，见下方动态状态；不回改本表 114 件的历史计数。 |

另有已按规则忽略的本机参考/产物：独立 `opencode/`、Tika JAR、Celery Beat 状态、`backend/archive/` 和本机旧 `docs/archive/2026-07/`。它们**不在上述 114 个可见文件之内**；忽略仅表示不进入主仓待办，不等于可以删除。独立 `cpa-xingtu/` 的未跟踪文件也不计入主仓。

**提交后的本机保护补充：**按用户澄清，项目技术文档可单独提交；竞赛/知识产权等非技术交付文件不得随之提交或推送。本机 `.git/info/exclude` 现仅在本机隐藏 `ip-materials/`、根目录四件竞赛文件、暂缓 branding 素材及两份含真实简历审计信息的 2026-10-10 报告；文件仍在原位，排除规则不随 Git 克隆分发。上表保留的是设置排除前的完整盘点快照；不要因它们不再出现在普通 `git status` 中就以为已删除，亦不要对这些路径使用 `git add -f`。

## 文件区域：先定性，再处理

- **当前源码/配置**：`backend/`、根目录 Compose/环境模板、`docs/`。改动需按各自测试和真实路径核验。
- **独立仓库/上游参考**：`cpa-xingtu/`、`opencode/`。不要把它们并入主仓 Git 状态或当成本项目已运行代码。
- **历史与运行产物**：`archive/`、`output/`、模型缓存及 Celery beat 状态文件。保留现状；清理前须核对引用、运行进程与恢复需求。
- **暂缓能力**：`workspace/default/branding/` 不是当前承诺的 live workspace 功能；边界见 [`CONTEXT.md`](../CONTEXT.md)。
- **待分类小文件（动态）**：根目录原有 `query`、`r.name.includes('jsdelivr')).length`；2026-10-17 16:15–16:49 又至少出现八件以 JS 表达式片段或中文词命名的小文件，例如 `x.outerHTML.slice(0`、`x.innerText)`、`x.href).slice(0`、`x.innerText.trim()`、`({type`、`x.getAttribute('placeholder')).join('`、`x.textContent.includes('新建工单')).length`、`报名`。抽查内容只有 `result: None` 或空文件，产生者/用途未确认，且同日有继续新增迹象；不得因表面像调试输出而删除或提交。接手时重新盘点，历史表的 2 件不代表当前总数。

## 第二轮分流（不等于删除）

- 已确认 `opencode/` 是独立、干净的上游参考仓库（本地 commit `388406238b`）；将它从主仓 Git 待办中排除，仍可按[上游复用记录](architecture/agent-upstream-adaptation.md)读取。
- 已确认 `backend/celerybeat-schedule.*` 是 Beat 本机状态文件，`models/tika/tika-app-3.3.3.jar` 是本机第三方解析组件；两类改由 `.gitignore` 排除，不改变文件本身。
- `.opencode/MIGRATION_PLAN.md` 是一份“迁移高级功能”草案，其“待执行”表格不能替代当前已采用的开源优先与薄适配边界；22 个新模块也不因为与草案同名就自动进入主线。
- `.ulpi/design/`、`ip-materials/`、竞赛文档、未跟踪的部署源码/测试和两份报告仍保持可见；它们是待确认的设计/交付/工作成果，不按缓存忽略。
- 两个根目录小文件内容分别为 `Docker Desktop Service` 和 `result: None`，疑似调试输出；已确认内容但未确认产生者，暂不移除。

## 第三轮归档与交接提醒

- `CODE_WIKI.md`（2026-06 生成）、早期 `dev_log.md`/`bug_log.md`、两份旧数据库设计稿已移入 `docs/archive/2026-10/`，并在新位置加归档说明。逐行核对了五份归档正文与 Git 中原版：除新增说明外，正文完全一致。
- 根目录中英文 README 已把旧 Code Wiki 从“现行运行指南”改为历史入口；`docs/README.md` 不再把被忽略的本机 `.trae/` 规则当作可分发的 schema 权威。`jd_cleaner_usage.md` 因相关工具仍有代码引用，暂留原位待单独验证。
- 为使这五份新归档能被 Git 看见，`.gitignore` 的 `archive/` 改为只忽略根目录 `/archive/`；此前未跟踪的 `docs/archive/2026-07/` 与 `backend/archive/` 仍单独保持本机忽略，不借本轮整理意外加入。提交前 Git 把旧路径显示为删除、新归档目录显示为未跟踪；这两侧需成对处理，不可对新归档执行 `git clean`。

## 本轮整理边界

提交前核验快照中主仓位于 `main`：**13 个已跟踪修改、5 个旧路径删除、46 个未跟踪状态条目（展开为上表 114 个文件）、0 个暂存文件**；独立 `cpa-xingtu/` 仓另有 **11 个未跟踪状态条目**，不计入主仓。初次盘点是 13 个修改/47 个未跟踪条目；整理新增归档和报告，同时将一个后端旧归档目录恢复为本机忽略，不能比较两个数字来推断丢失文件。未跟踪目录在 `git status` 中按一个条目计，接手时必须重新运行。五个旧路径显示为删除、新归档目录显示为未跟踪，只是当时尚未暂存的移动，不代表正文被丢弃。本轮未删除原文内容或重置其他工作。后续每条工作流应单独核验、单独收束；完成证据放对应 guide/runbook/report，本页只保留简短状态与入口。
