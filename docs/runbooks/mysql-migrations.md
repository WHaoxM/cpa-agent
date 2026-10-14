# MySQL 结构迁移（统一入口）

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

   `current` 应等于 `heads`，目前为 `0004_resume_trace_schema`。第一次 `current` 为空表示尚未被 Alembic 管理，并不代表数据库为空。**不要直接 `stamp head`**：它只写版本号，不执行 DDL，可能造成假成功。新库也在 Docker 初始化完成后运行同一个 `upgrade head`。
3. 核验 `alembic_version` 只有预期 head；检查账号 `auth_epoch` 触发器、管理员暂存列、`job_catalog_source_listing`、Pipeline 事件表及投影表；再启动后端/worker 并走受影响 HTTP 业务路径。版本号不是业务验收。

`0001` 幂等创建冻结快照中的缺表；`0002` 以有界存在性检查补账号撤销触发器、管理员私有来源列与来源专用岗位表；`0003` 补曾由分散安装命令创建的审阅/投影/事件表、Pipeline 触发器和章节视频列。它们不回填、不发布岗位、不改变已有业务行。MySQL DDL 会独立提交；中途失败可能留下部分对象，**不能通过回滚 SQL 事务恢复**。这些版本按缺失对象可重试，但在重试前应根据错误核对实际结构，不能无条件删表/列或修改旧迁移文件。

现存很早的库可能有其他结构漂移，`CREATE TABLE IF NOT EXISTS` 不会修复同名旧表的任意列/索引差异。遇到升级失败时保留现场和备份，检查 `information_schema`；先写一个新的、有前置条件和回归测试的修复版本，或在隔离副本验证一次性桥接，不要 `stamp` 越过失败版本。现有部分兼容代码仍有懒建表/补列逻辑；这不是新结构变更入口，后续应逐处移除并用实际 HTTP 链路验证。当前 Alembic 不管理 Neo4j/Milvus schema、SQL 种子数据、LangGraph 自有 checkpoint 表或文件上传卷。

`0004` 接管 L0 简历持久派发与 owner-scoped Agent trace 表；此前使用显式 `--install` 建表的环境由此版本验证并纳入版本账本，不删除已有任务或轨迹。

### 维护窗口的备份、验收与回退

`current` 为空的既有库可能已经有全部业务表和触发器；这只说明没有版本账本，**不能**据此直接 `stamp`。在不停业务的只读预检中，先查 `information_schema` 中的表、必要列及触发器；如需验证迁移兼容性，仅把 `SHOW CREATE TABLE` 和触发器定义复制到唯一命名的临时库，在该临时库运行 `-x database=<临时库名> upgrade head`，核对版本及对象后只删除这个临时库。结构副本不含业务行，不能替代下述备份或业务验收。

实际接管必须安排停写窗口：阻止新请求，停止 Flask 和 Beat，待 `cpa.pipeline` worker 的在途任务完成后停止 worker；核对三个进程都已停止，再备份。不要仅停止前端，或在 worker 仍可能写 `pipeline_task` 时导出。备份应成对覆盖目标 MySQL 库及 `backend/uploads/`（含简历、Agent 附件、管理员私有来源）；文件与 SQL 放到空间足够的非系统盘。Windows 上使用 MySQL 8 `mysqldump.exe` 的 `--result-file`，不要用 PowerShell 5.1 的文本重定向写 SQL，以免改变编码。示例（在维护窗口人工替换数据库账号、主机与端口；`--password` 在终端交互输入，不将密码写进命令行）：

```powershell
$repo = 'D:\AGeneral Workspace\cpa-agent'
$dbUser = '<数据库管理员账号>'
$backupDir = Join-Path 'D:\cpa-backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$dump = Join-Path $backupDir 'cpa_agent_before_alembic.sql'
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe' `
  --host=127.0.0.1 --port=13306 --user=$dbUser --password `
  --single-transaction --quick --routines --triggers --events `
  --set-gtid-purged=OFF --no-tablespaces --result-file=$dump cpa_agent
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
  --database=$restoreDb --execute="source $dumpForMysql"
if ($LASTEXITCODE -ne 0) { throw 'SQL 恢复演练失败；不要迁移' }
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' `
  --host=127.0.0.1 --port=13306 --user=$dbUser --password `
  --database=$restoreDb --execute="SELECT COUNT(*) FROM user_account; SHOW TRIGGERS"
```

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
