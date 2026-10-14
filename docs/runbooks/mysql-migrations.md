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

## 每次新增结构变更

在 `backend/` 使用 `python -B -m alembic -c alembic.ini revision -m "change_name"` 生成新文件。模板中的 `upgrade()` 故意抛 `NotImplementedError`，必须先写清迁移及验证，不能把空版本提交。

在 `backend/migrations/versions/` 新增版本文件，`down_revision` 指向当前 head；不要改已发布版本、`init_mysql.sql` 或旧单次 SQL。使用明确的 `upgrade()`、存在性检查和数据保留策略；MySQL DDL 非事务性，必须可在半完成后重试。对不可逆变更先备份，`downgrade()` 不得暗中删业务数据。人工审核迁移 SQL/对象、在一次性真实 MySQL 库验证空库与旧库升级/重复运行，并验证受影响 HTTP 路径及权威数据状态。不要把“`upgrade head` 退出 0”当作产品功能通过。

回归命令（真实 MySQL `localhost:13306` 可用时）：

```powershell
$env:CPA_TEST_LIVE_MYSQL='1'
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_mysql_migrations.py
```

该测试在 MySQL 内创建唯一的临时数据库、模拟旧库缺列与保留账号行、升级两次并核验版本/触发器/行/表，结束后仅删除自己创建的临时数据库；不修改配置的 `cpa_agent` 业务库。无 MySQL 时两个纯单测仍可运行，真实接管验收未完成。
