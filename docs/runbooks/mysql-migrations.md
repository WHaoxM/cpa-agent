# MySQL 结构迁移（统一入口）

2026-10-23 本机正常 `cpa_agent` 已在停写、全库/上传备份、临时库恢复和迁移演练后，由0017经0018/0019升级到 **0020_career_pathway**。0020追加公共参考链七表，不改旧课程collation或个人行；首轮演练发现外键collation不一致，仅在临时副本失败，修正未发布的新迁移后才升级正常库。74张旧基表原字段/行和触发器迁移前后校验一致。备份、失败重试、真实三库/页面与剩余范围见[回放证据](../reports/2026-10-23-backend-development-pathway-replay.md)。此为本机结果，不代替其他实例的独立迁移。MySQL DDL部分执行的重试检查仅覆盖列/类型/主外键，不是完整漂移修复器。

`0017_resume_draft_guidance` 仅给已有 `student_resume_draft` 增加可空 JSON 来源指引；旧草稿保持可读，客户端不能直接写该来源。2026-10-22 用户授权后，已对本机正常库执行 **0014→0015→0016→0017**：停写备份、上传逐文件校验、临时库恢复与迁移演练、原行／触发器核对，再升级和重载服务。证据与一次任务读取中断的边界见[正常入口回访](../reports/2026-10-22-real-page-experience-repair.md#5174-正常入口备份迁移重载与真实回访)。这不证明其他部署已升级；后续仍须按目标实例实际版本、备份和维护窗口执行，不能直接 `stamp` 或跳过中间迁移。下方旧“正常库0014”等段落属于此前阶段，不覆盖这次现场结果。

MySQL **新增结构变更**从 2026-10-14 起由 Alembic 的 `alembic_version` 记录。Alembic 迁移是部署动作，不在 Flask 启动、HTTP 请求、Celery 任务或种子数据导入时自动执行；少数历史兼容代码仍会懒建表/补列，见下文剩余边界。`backend/scripts/mysql/init_mysql.sql` 是已冻结的旧版建库快照；Docker MySQL 首次建卷仍会执行它，但既有 volume 不会重放。不要再修改这份快照或旧 `migration_*.sql` 来升级实例；新增结构只增添 `backend/migrations/versions/` 中的版本文件。旧 SQL/`--install` 命令仅保留供历史追溯和尚未切换的旧部署使用。

## 升级或接管既有库

1. 先备份目标 MySQL 数据库及相关上传卷；确认配置的 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE` 指向目标库。在维护窗口停写、停止 Flask/worker/Beat，且只允许一个迁移进程运行。
2. 从仓库 `backend/` 目录、`mkg` 环境运行：

   ```powershell
   D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m alembic -c alembic.ini heads
   D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m alembic -c alembic.ini current
   D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m alembic -c alembic.ini upgrade head
   D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m alembic -c alembic.ini current
   ```

   `current` 应等于**本次固定检出版本**的 `heads`；2026-10-22 本机正常开发库已按备份/恢复演练流程从 `0010` 升到 `0014_student_resume_draft`，见[实际证据](../reports/2026-10-22-student-resume-builder-replay.md#正常开发环境接通与完整页面回访)。旧实例可能只到更早版本，不从本机结论推断其他部署状态。第一次 `current` 为空表示尚未被 Alembic 管理，并不代表数据库为空。**不要直接 `stamp head`**：它只写版本号，不执行 DDL，可能造成假成功。新库也在 Docker 初始化完成后运行同一个 `upgrade head`。
3. 核验 `alembic_version` 只有预期 head；检查账号 `auth_epoch` 触发器、管理员暂存列、`job_catalog_source_listing`、Pipeline 事件表及投影表；再启动后端/worker 并走受影响 HTTP 业务路径。版本号不是业务验收。

生产 Compose 候选层提供与 HTTP/worker/Beat **同一 digest** 的显式 `migrate` profile，可代替宿主机 Python 迁移命令，但**不能**跳过维护窗口、停写、备份及恢复演练。上传文件位于 `${CPA_COMPOSE_PROJECT_NAME}_backend_uploads` 命名卷；下文 `backend/uploads/` 宿主机备份示例不可用于该卷。生产层已不允许现场 `docker compose build backend`；先完成 [生产级镜像改造计划](../specs/production-image-hardening.md) 中的来源与镜像验收，才按 [DEPLOY.md](../../DEPLOY.md) 的发布门禁和具体环境编排。`migrate` 不是 `up` 的依赖，也不自动运行；运行后必须比较 Alembic `current` 与本次镜像 `heads`，再做真实业务回读。

`0001` 幂等创建冻结快照中的缺表；`0002` 以有界存在性检查补账号撤销触发器、管理员私有来源列与来源专用岗位表；`0003` 补曾由分散安装命令创建的审阅/投影/事件表、Pipeline 触发器和章节视频列。它们不回填、不发布岗位、不改变已有业务行。MySQL DDL 会独立提交；中途失败可能留下部分对象，**不能通过回滚 SQL 事务恢复**。这些版本按缺失对象可重试，但在重试前应根据错误核对实际结构，不能无条件删表/列或修改旧迁移文件。

现存很早的库可能有其他结构漂移，`CREATE TABLE IF NOT EXISTS` 不会修复同名旧表的任意列/索引差异。遇到升级失败时保留现场和备份，检查 `information_schema`；先写一个新的、有前置条件和回归测试的修复版本，或在隔离副本验证一次性桥接，不要 `stamp` 越过失败版本。现有部分兼容代码仍有懒建表/补列逻辑；这不是新结构变更入口，后续应逐处移除并用实际 HTTP 链路验证。当前 Alembic 不管理 Neo4j/Milvus schema、SQL 种子数据、LangGraph 自有 checkpoint 表或文件上传卷。

`0004` 接管 L0 简历持久派发与 owner-scoped Agent trace 表；此前使用显式 `--install` 建表的环境由此版本验证并纳入版本账本，不删除已有任务或轨迹。

`0005` 增加管理员学校档位审核/撤回账本 `school_tier_review`，不改写已确认的学生 L0 原文，也不自动把旧 partial 画像变为可评分。迁移前旧 L2 无审核引用的结果仍可读取/生成，管理员审核入口返回 503；迁移并完成正式账号与官方来源审核后才可用。当前 live 业务库尚未接管 Alembic，不能在运行中的 Flask/worker 上直接升级；仍按维护窗口备份、停写、恢复演练和业务链路验收操作。

`0006` 增加确认后自动画像的 `pipeline_portrait_dispatch` 与简历诊断的 `resume_diagnostic_dispatch`。两表以 `pipeline_task.task_id` 为外键并随测试任务清理级联删除；迁移会验证所需列，并接管旧显式安装器创建的同名表及缺失外键，不改写已有成果或任务。当前 live 业务库曾用显式安装器加这两表作开发验证，**不等于**已完成统一 Alembic 接管；正式实例仍须在维护窗口升级 `head`。迁移完成、worker 与 Beat 均启动后，确认操作才能留下可消费的后续任务；缺表不能靠页面重试解决。

`0007` 增加 `student_portrait_snapshot`：新生成的 L2 部分/完整结果以 `source_task_id` 唯一保存版本、来源确认任务、自述修订、原始画像 JSON 与内容摘要；空维度仍为空，不修改旧 `student_portrait_4d` 评分表。已有画像不自动回填，尤其不能把旧 `NOT NULL DEFAULT 0` 解释为已证实的零分。当前本机运行库用显式安装器先加了此表作开发验收，仍未完成 Alembic 账本接管；正式升级按同一维护窗口流程执行。新代码发布前必须先迁移，不能让 L2 在缺表时开始执行。

`0008` 增加 Agent owner-bound run、不可变计划版本、串行步骤和按 run 单调递增的事件表；还保存限长、字段白名单、带 schema/redaction 版本的输入快照，以及最终结果哈希。最后一步完成与 Agent 最终回复持久化采用不同状态，只有最终结果和 `run.completed` 事件同事务提交后才是 `completed`。父线程曾在自动创建/清理的独立 MySQL schema 上验证当时的 clean/legacy/repeated upgrades 和 repository 状态流；这不代表默认开发库或生产库已经迁移。

`0009` 增加 `agent_run_dispatch` MySQL outbox、publish lease、worker claim lease 与重放索引；初始 run/outbox/event 在 HTTP submit 的同一 MySQL 事务内提交，随后才尝试向独立 `cpa.agent` Celery 队列发布。0009 migration 不会自动派发升级前旧 API 收到的 `queued` 请求，而将它们标为 `not_dispatched`；只有 owner 显式以相同 idempotency key 再提交原请求才释放该 hold。worker payload 只有 run ID；当前 worker 只 claim 并标记 `waiting_executor`，不调用 Agent/model/tool，run status 仍是 `queued`。消息送达、Celery ACK、MySQL 业务状态分别表达，不使用 result backend 作真值。publish 失败可由 Beat 依据 MySQL outbox 有界重放；若 claim 在任何 plan/step 前过期可安全重派，若 run/step 已有执行进度或副作用不确定则进入 `needs_reconciliation` 且禁止自动重跑。

**隔离验收（2026-10-17）：**`CPA_TEST_LIVE_MYSQL=1` 的迁移用例在自动创建/清理的唯一 schema 上 **4 passed**（clean/legacy、重复升级与 `0009` head）；另用空 Redis DB 12、唯一 MySQL schema 与独立 `cpa.agent` solo worker 跑 transport smoke **1 passed**，MySQL 结果为 run `queued` / dispatch `waiting_executor` / plan version 0 / 4 个真实顺序事件。未接 Agent executor、真实模型、业务工具或前端，也没有升级默认开发库/生产库。首次手动隔离 worker probe 因子进程 `Config` 的 `load_dotenv(..., override=True)` 重新采用仓库 `.env`，对默认 `cpa_agent` 执行过一次只读查询并因缺 `agent_run` 表失败；没有对该库迁移或写入。后续通过的 smoke 使用测试专用 `celery_transport_probe` 将 worker 配置覆盖至唯一临时 schema。部署镜像中的 `.env` 携带情况和真实生产容器环境变量优先级仍待核验，不据此推断生产行为。详见 [Agent 持久运行状态层](../guides/agent-run-persistence.md)。

### 维护窗口的备份、验收与回退

**本机状态以本次核验为准：**下文迁移版本段落中“live 尚未接管”的措辞是早期开发验证现场，不是所有部署的永久状态；2026-10-22 正常本地 `cpa_agent` 已升级到 `0014`。其他实例与生产卷仍须单独验收，不能用本机升级替代。

`current` 为空的既有库可能已经有全部业务表和触发器；这只说明没有版本账本，**不能**据此直接 `stamp`。在不停业务的只读预检中，先查 `information_schema` 中的表、必要列及触发器；如需验证迁移兼容性，仅把 `SHOW CREATE TABLE` 和触发器定义复制到唯一命名的临时库，在该临时库运行 `-x database=<临时库名> upgrade head`，核对版本及对象后只删除这个临时库。结构副本不含业务行，不能替代下述备份或业务验收。

单机生产 Compose 的 MySQL 与上传均在 Docker 命名卷，**不能使用下方宿主机 `backend/uploads/` 复制示例**。先按 [DEPLOY.md 的冷卷快照与全新项目恢复演练](../../DEPLOY.md) 停所有写入者及数据库，再处理已有生产卷；快照工具拒绝仍被运行中容器占用的源卷、校验归档及恢复后的逐文件摘要，但尚未完成真实卷恢复演练。缺此验收时不得升级已有生产卷。下述 `mysqldump`/宿主机目录流程仅适用于非 Compose 本地部署。

实际接管必须安排停写窗口：阻止新请求，停止 Flask 和 Beat，待 `cpa.pipeline` / `cpa.agent` worker 的在途任务完成后停止 worker；核对三个进程都已停止，再备份。不要仅停止前端，或在 worker 仍可能写 MySQL 业务表时导出。备份应成对覆盖目标 MySQL 库及 `backend/uploads/`（含简历、Agent 附件、管理员私有来源）；文件与 SQL 放到空间足够的非系统盘。Windows 上使用 MySQL 8 `mysqldump.exe` 的 `--result-file`，不要用 PowerShell 5.1 的文本重定向写 SQL，以免改变编码。示例（在维护窗口人工替换数据库账号、主机与端口；`--password` 在终端交互输入，不将密码写进命令行）：

```powershell
$repo = 'D:\AGeneral Workspace\cpa-agent'
$dbUser = '<数据库管理员账号>'
$backupDir = Join-Path 'D:\cpa-backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$dump = Join-Path $backupDir 'cpa_agent_before_alembic.sql'
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe' `
  --host=127.0.0.1 --port=13306 --user=$dbUser --password `
  --single-transaction --quick --routines --triggers --events `
  --default-character-set=utf8mb4 --set-gtid-purged=OFF --no-tablespaces --result-file=$dump cpa_agent
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $dump) -or `
    (Get-Item -LiteralPath $dump).Length -eq 0) { throw 'MySQL 备份失败；不要迁移' }
Copy-Item -LiteralPath (Join-Path $repo 'backend\uploads') `
  -Destination (Join-Path $backupDir 'uploads') -Recurse -Force -ErrorAction Stop
$sourceFiles = @(Get-ChildItem -LiteralPath (Join-Path $repo 'backend\uploads') -File -Recurse -Force)
$backupFiles = @(Get-ChildItem -LiteralPath (Join-Path $backupDir 'uploads') -File -Recurse -Force)
if ($sourceFiles.Count -ne $backupFiles.Count -or `
    ($sourceFiles | Measure-Object Length -Sum).Sum -ne `
    ($backupFiles | Measure-Object Length -Sum).Sum) { throw '上传文件备份不完整；不要迁移' }
Get-FileHash -Algorithm SHA256 -LiteralPath $dump
```

记录导出文件大小/哈希、上传文件数及路径，并将 SQL **先恢复到另一个临时数据库**核对关键行和触发器；备份文件存在不等于可恢复。以下恢复方式已在一次性 MySQL 库验证可保留行和触发器；`$restoreDb` 仅含字母、数字和下划线，不得设为业务库名：

```powershell
$restoreDb = 'cpa_restore_' + (Get-Date -Format 'yyyyMMddHHmmss')
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' `
  --host=127.0.0.1 --port=13306 --user=$dbUser --password `
  --execute="CREATE DATABASE $restoreDb CHARACTER SET utf8mb4"
if ($LASTEXITCODE -ne 0) { throw '无法创建恢复演练库；不要迁移' }
$dumpForMysql = $dump.Replace('\', '/')
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' `
  --host=127.0.0.1 --port=13306 --user=$dbUser --password `
  --default-character-set=utf8mb4 --database=$restoreDb --execute="source $dumpForMysql"
if ($LASTEXITCODE -ne 0) { throw 'SQL 恢复演练失败；不要迁移' }
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' `
  --host=127.0.0.1 --port=13306 --user=$dbUser --password `
  --default-character-set=utf8mb4 --database=$restoreDb --execute="SELECT COUNT(*) FROM user_account; SHOW TRIGGERS"
```

Windows 的 `mysql.exe` 客户端可能默认 GBK，即使目标库是 utf8mb4；导入 UTF-8 SQL 必须显式指定 `--default-character-set=utf8mb4`。本机实际恢复曾因默认 GBK 报 `Unknown command`，指定 UTF-8 后同一 SQL 原字节恢复成功；不要重编码备份或据此修改业务库数据。上传备份除数量/总字节外，还应逐文件比较 SHA-256，不能以同名同大小替代内容一致。

将恢复库中的关键表行数、触发器与原库逐项比较；演练库在最终验收前保留，不要误清理。确认后按上文执行 `upgrade head`，核对 `current=head`、账户 `auth_epoch` 触发器、来源岗位/任务事件/简历轨迹表，再用真实登录、任务创建与 owner-scoped 查询走一遍受影响 HTTP 路径，最后恢复服务。迁移是 MySQL 非事务性 DDL；失败时不要 `downgrade`、`stamp` 或假定 SQL `ROLLBACK` 已撤销。保留失败库现场，先查实际对象和错误；若必须回退，保持停写，从备份恢复到**新数据库**并验证，再将部署的 `MYSQL_DATABASE` 指向它、核对上传文件副本后重启。原失败库留作排障，切勿直接覆盖或删除。MySQL 恢复不自动回滚 Redis、Neo4j、Milvus 或文件系统，跨存储写入需另行核对。

## 每次新增结构变更

在 `backend/` 使用 `python -B -m alembic -c alembic.ini revision -m "change_name"` 生成新文件。模板中的 `upgrade()` 故意抛 `NotImplementedError`，必须先写清迁移及验证，不能把空版本提交。

在 `backend/migrations/versions/` 新增版本文件，`down_revision` 指向当前 head；不要改已发布版本、`init_mysql.sql` 或旧单次 SQL。使用明确的 `upgrade()`、存在性检查和数据保留策略；MySQL DDL 非事务性，必须可在半完成后重试。对不可逆变更先备份，`downgrade()` 不得暗中删业务数据。人工审核迁移 SQL/对象、在一次性真实 MySQL 库验证空库与旧库升级/重复运行，并验证受影响 HTTP 路径及权威数据状态。不要把“`upgrade head` 退出 0”当作产品功能通过。

回归命令（真实 MySQL `localhost:13306` 可用时）：

```powershell
$env:CPA_TEST_LIVE_MYSQL='1'
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_mysql_migrations.py
```

该测试在 MySQL 内创建唯一的临时数据库、模拟旧库缺列与保留账号行、升级两次并核验版本/触发器/行/表，结束后仅删除自己创建的临时数据库；不修改配置的 `cpa_agent` 业务库。无 MySQL 时两个纯单测仍可运行，真实接管验收未完成。

Celery/Beat 与迁移的隔离联验（真实 MySQL `localhost:13306`、Redis `localhost:16379` 可用，Redis 逻辑 DB 13、14 必须为空）：

```powershell
$env:CPA_TEST_LIVE_MYSQL='1'
$env:CPA_TEST_LIVE_CELERY='1'
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_celery_deployment.py
```

该专项检查生产 Beat 的 6 个任务注册/路由，再用测试专用 schedule/队列验证真实 Beat 投递与 solo worker 消费；并在唯一临时 MySQL 库投递生产 `cpa.pipeline.match`，核验缺 L2 画像时权威任务行变为 `partial`、派发次数为 1、`reason=confirmed_portrait_unavailable` 且没有匹配成果行。Agent 专用 live smoke 使用空 Redis DB 12、唯一临时 MySQL schema 和独立 `cpa.agent` worker；只证明提交→运输→claim→`waiting_executor` 的持久状态，确切命令见 Agent 持久运行指南。测试只清理自身临时库和 Redis queue/control keys，不调用业务库，不向生产队列投递。它覆盖**运输和一个受阻业务分支**，不代表完整 L0/OCR/L2/L3 或 Agent 成功链路、现有业务库升级或生产 Compose 镜像的验收。
