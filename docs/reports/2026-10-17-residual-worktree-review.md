# 剩余技术工作树续审（2026-10-17）

这是 `codex/workspace-context-cleanup` 的本机**审查快照**，不代表部署或 Agent 功能验收。依据是当时的 `git status`、未跟踪文件展开、当前路由/构建配置、Docker 只读盘点和下述测试；文件归属不能由路径推断。

## 分流结果

| 范围 | 数量与证据 | 处置 |
|---|---|---|
| 部署候选 | 11 个已跟踪修改：`.env.example`、`DEPLOY.md`、中英文 README、Dockerfile、requirements、三份既有测试、生产 Compose、MySQL 迁移手册；另有 WSGI、三份运维脚本、四份测试、host-proxy 覆盖层及镜像提案共 10 个未跟踪文件。 | 作为**一个独立候选交付单元**保留本机；不与文档整理或 Agent 实验混合提交。证据与停止条件见[部署分流核查](2026-10-17-deployment-worktree-triage.md)。 |
| Agent 基础能力实验 | 22 个未跟踪 Python 文件（runtime 5、utils 17），用 `mkg` Python 3.10 逐个 `ast.parse` 均通过；受控 HTTP 两轮会话回放 `1 passed`，默认路由进入替身 Supervisor，尚无这 22 个候选模块的接线证据。 | 不批量接入或提交；按[专项核查](2026-10-17-untracked-agent-modules-triage.md)先证明现行缺陷、上游适配和真实请求/状态/预算链路。替身测试与静态搜索不证明真实模型验收或动态加载绝对不可能。 |
| `.ulpi/design/` | 2 份落地页设计语言/交互规格，明确指向独立前端仓 `cpa-xingtu/src/views/LandingView.vue`；该路由和页面存在，但草案指定的 `CoordinateCard`/`Quiz` 组件未见于当前页面/组件检索，不能把草案写成已交付。独立仓另有未跟踪设计与截图。 | 归属及与前端当前实现的关系待前端工作流核对，不移入主仓源码或混入后端提交。 |
| `.opencode/MIGRATION_PLAN.md` | 1 份旧高级功能迁移草案，表内仍写“待执行”，但相应候选源码已在本机出现。 | 只作思路来源；不能覆盖当前 Agent 开源复用和真实链路门槛，待作者确认后再决定是否归档。 |
| 根目录 `query` 与 `r.name.includes('jsdelivr')).length` | 2 个来源未明的小文件，内容分别为 `Docker Desktop Service`、`result: None`。 | 内容已确认，产生者未确认；保留可见，不删除、不提交。 |

上述可见未跟踪数为 **37 = 10 + 22 + 2 + 1 + 2**。另有本机 `.git/info/exclude` 隐藏的 69 个非技术交付/敏感证据文件（知识产权 62、竞赛 4、branding 1、真实简历审计 2）；它们仍在磁盘上，不随仓库分发，也不计入 37。仅本机忽略不能替代其他克隆的发布审查。

## 部署候选的本轮实证与停止点

- Docker Engine 28.5.2 可访问，但盘点时 `docker ps` 无运行容器；本机既有应用镜像不能绑定到本轮脏工作树。未构建新镜像，未触碰既有业务卷。
- 五份专项测试经 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_compose_deployment.py backend/tests/test_release_preflight.py backend/tests/test_production_wsgi.py backend/tests/test_volume_archive.py backend/tests/test_volume_snapshot.py` 得 **41 passed**。两例新回归修正前均失败，证明静态预检曾允许 worker/Beat 与 HTTP 使用不同环境；本机候选预检现拒绝该差异。实际 Compose 解析模型使用占位凭据与摘要时，基线检查返回 `[]`，只改 worker `CPA_DEMO_MODE=true` 则返回一致性错误；预检 CLI 返回的 PASS 明确限定为静态配置。
- 没有经审查的干净构建输入、固定依赖/基础镜像、registry digest、真实命名卷恢复、HTTPS 或 owner-bound 业务结果；**不提交部署候选为可发布代码，不启动生产卷升级**。本机预检修正与测试仍须和候选脚本一同接受后续审查。

## 已收束与下一核验

`.env.production` 和根目录 `output/` 经检查均无已跟踪文件命中，作为本机敏感配置/生成产物的忽略规则已单独提交（`d8267ee`）；这不等于部署候选获准发布。下一步先冻结部署来源、排除 22 个 Agent 候选进入镜像构建，再在隔离新卷上做真实恢复与服务/用户结果验收。设计与草案需各自归属工作流确认；来源不明的小文件保持原位。任何组别只有证据达到其验收门槛才独立提交推送。

续核时主仓本轮导航、Agent 和部署三份报告中的相对 Markdown 链接逐个解析，未见失效目标。独立前端仓 Git 状态仍有 **11 个折叠条目、展开 13 个未跟踪文件**；其 `AGENTS.md` 本地不存在。主仓不能代表前端仓认领、移动或提交这些文件。
