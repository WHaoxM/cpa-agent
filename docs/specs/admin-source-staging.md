# 管理员私有来源暂存（非岗位入库 / 非建图）

> 迁移入口已改为 [MySQL 统一迁移](../runbooks/mysql-migrations.md) 的 `alembic upgrade head`。下文的单次 SQL 操作说明仅保留历史验收背景，不再用于新部署。

## 当前契约

`POST /api/admin/dataset/upload` 与 `POST /api/admin/knowledge-base/upload` 原有 JSON 请求继续**只创建元数据**，返回 `pending`、`content_ingested=false`；客户端自报的 `status/count` 不生效。新增的同路由 `multipart/form-data` 分支必须含 `title` 和 `file`，仅服务端管理员可调用：

- dataset：`.json`、UTF-8、顶层 1–1000 项数组，每项对象须有 1–200 字符的 `title`。服务端解析条目数写入 `staged_record_count`；`job_dataset.count` 仍为 **0**，因为没有任何权威岗位行被创建、核对或发布。
- knowledge-base：`.txt`、UTF-8、非空正文。不运行 LLM、实体解析、技能词典更新或图谱写入。
- 两者均限 2 MiB；扩展名、字节数和内容在落盘前校验。私有文件写入 `Config.UPLOAD_FOLDER/admin_sources/`，数据库记录路径、长度、SHA-256 和服务端管理员 ID。`status=pending`、`content_staged=true`、`content_ingested=false` 表示仅原文暂存。

`GET /api/admin/{dataset|knowledge-base}/<id>/content` 管理员回读完整暂存原文；每次回读检查固定 ID 路径、常规文件、大小、SHA-256，dataset 还检查解析条目数。旧元数据记录返回 409；来源文件缺失、路径不安全或摘要不符返回 503，不会把失效文件作为真值。`DELETE /api/admin/{dataset|knowledge-base}/<id>` 删除数据库行及对应私有文件；删除事务失败时恢复文件。所有这些路由均经过现有服务端管理员门禁。

暂存不会**自动**生成公开岗位目录、`job_posting`、`job_catalog_publication`、技能/知识图谱或 L3/L4 数据。`POST /api/admin/pipeline/build` 仍明确返回 503 且不创建任务。暂存文件的来源真实性与业务字段尚未审核；不能仅凭 JSON 可解析、SHA-256 或上传者管理员身份认定其为可信 JD。明确选定的单条 dataset 记录现在可以进入独立的正式管理员预检→人工确认→发布流程；服务端两次重读来源并把 dataset/record 摘要写入发布快照，见 [dataset 来源绑定](admin-dataset-publication-binding.md)。这仍不表示暂存内容是官方来源或可以自动评分。

## 部署与恢复

既有 MySQL volume 首次启用 multipart 前，**一次性**执行 `backend/scripts/mysql/migration_admin_source_staging.sql`，重启后端；初始化新库的 `init_mysql.sql` 已含新列。旧行 `content_sha256=NULL`、dataset `staged_record_count=0`，仍按元数据处理。若尚未迁移，上传事务失败且已暂存文件会被清理，不返回伪成功。存储根目录必须是后端可写、仅管理员服务可读、跨进程共享的持久卷；默认 `backend/uploads/` 不等于容器或机器重建后的持久性保证。

文件系统与 MySQL 不具备共同事务：正常异常路径会清理/恢复文件，但进程在文件改名与数据库提交之间被强杀仍可能留下无数据库引用的私有孤儿文件，需按 `job_dataset.file_path`、`knowledge_base.file_path` 对照 `admin_sources/` 做运维核查后清理，不能通过扫描目录自动发布。备份/恢复必须同时覆盖数据库与私有来源卷；回读摘要失败时从原始上传重新暂存，不从被篡改文件继续处理。

## 验收与复用边界

2026-10-14：先复现旧 multipart 对两个入口均返回 400；迁移本机 MySQL 后，运行 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_admin_source_staging_http_mysql.py backend/tests/test_admin_catalog_upload_status_mysql.py backend/tests/test_admin_catalog_metadata_mysql.py backend/tests/test_admin_catalog_input_contract.py`，20 passed。测试经 Flask 路由（其中一条使用真实 TCP socket）、MySQL `localhost:13306` 与临时私有文件目录核对上传、回读、篡改拒绝、删除、DB 故障回滚、路径围栏、大小限制及非管理员拒绝；管理员 Principal 在测试内注入，**不等于**真实登录会话端到端验收。此机器当时 C: 临时卷空间为 0，测试需设 `TEMP` / `TMP` 指向 D: 可写临时目录；大小门槛测试把常量临时缩到 8 字节验证同一判断，不让测试框架向 C: 落 2 MiB multipart。

随后新增 `CPA_TEST_LIVE_MYSQL=1` 的 `backend/tests/test_admin_source_account_http_mysql.py`：经真实 TCP HTTP、平台自建管理员账号 CLI 创建/登录、Cookie/CSRF 与学生账号越权拒绝，暂存含两条岗位的 JSON；MySQL 回读 `uploaded_by` 为真实账号 ID，`count=0`、`staged_record_count=2`，文件及摘要一致，公开发布和旧岗位表行数均不变，管理员删除后 DB/文件清空。该独立验收 **1 passed**，不把私有暂存误认为已发布或已建图。

实现参考项目现有 `backend/app/services/agent_runtime/media/file_store.py` 的有界上传、SHA-256 与临时文件替换机制；同时检查本地 OpenCode 快照 `388406238bd5ca15564a762840a2362c3a45bd9c` 的 `packages/opencode/src/util/filesystem.ts` 基础文件写入。没有复用 `AgentFileService` 本体：其学生 owner、会话挂载、OCR/Celery 状态和任务协议不适用于管理员仅暂存原文；本链路没有新的通用文件/任务抽象，也没有异步解析。
