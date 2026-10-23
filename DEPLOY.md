# CPA-Agent 本地复现与部署边界

本文以**新克隆仓库、后端在宿主机运行、Docker 只承载 MySQL/Neo4j/Redis**为可复现路径。前端 `cpa-xingtu` 是[独立仓库](https://github.com/WHaoxM/cpa-xingtu)，不会随本仓库克隆。纯前端 Mock 演示无需本页的数据库、模型或后端；见前端仓库的 `README.md` / `DEPLOY.md`。

## 0. 前置条件与两种验收

- Docker Engine/Desktop + Compose v2，Python **3.10**、Conda（本机已验证的环境名为 `mkg`），以及真实后端联调所需的 LLM API 凭据。前端另需 Node `^20.19.0 || >=22.12.0`。
- **基础启动**：`GET /health` 和 `GET /api/pipeline/list` 可响应。这不证明登录、模型、异步任务或业务数据可用。
- **真实联调**：三种数据库健康、Alembic `current=head`、正式账号登录、受保护 API 与 owner 范围的数据库/任务结果都需单独核验。异步简历与 L3 还要运行 Celery worker；确认后的 L2/诊断扩展还须先核对本次检出是否包含对应迁移与代码。Beat 负责异常后的恢复扫描。
- 现有工作机上默认 `python` 是缺依赖的系统 Python，`cpa-agent` 环境也不完整；运行后端和测试前请先读 [README.md](README.md) 中“运行环境（Agent / 开发者必读）”。新机器可自行创建 Python 3.10 环境，不需要沿用 `mkg` 名称。

## 1. 从新克隆启动依赖

在本仓库根目录执行。Windows PowerShell 5.1 先设置 UTF-8 输出，读取中文文件使用 `Get-Content -Encoding UTF8`：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
Copy-Item .env.example .env
```

macOS/Linux 用 `cp .env.example .env`；后续 Docker、Conda、Python 命令相同。下面的 `Copy-Item` 也可对应换成 `cp`。实际端口受本机占用和环境变量影响，以 Compose `config` / `ps` 为准。

编辑**未提交的** `.env`：将 `SECRET_KEY` 换成随机且至少 32 字符；填写 `LLM_API_KEY` 和使用的 provider；为 MySQL/Neo4j 设置密码，确保 `MYSQL_PASSWORD` 与 `MYSQL_ROOT_PASSWORD` 一致。Compose 在仓库根目录读取 `.env` 作变量插值，Flask/worker 也读取此文件。模板 `.env.example` 默认 `CPA_DEMO_MODE=true`；要使用平台账号和真实学生数据，明确改为 `false`。仅本机 HTTP 联调时设 `CPA_AUTH_COOKIE_SECURE=false`、`FLASK_HOST=127.0.0.1`；对外部署必须恢复 Secure Cookie、HTTPS 和 `FLASK_DEBUG=false`。不要提交 `.env` 或真实密钥。

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d mysql neo4j redis
docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
```

等待三个服务均为 healthy。基础 `docker-compose.yml` 不发布数据库端口；叠加 dev 层后仅在 `127.0.0.1` 映射 MySQL `13306`、Neo4j HTTP `17668` / Bolt `17687`、Redis `16379`。以 `docker compose ... config` 检查最终映射。它是本机开发入口，不是公网部署配置。

首次创建 MySQL volume 时会运行 `backend/scripts/mysql/init_mysql.sql` 和 seed SQL；**已有 volume 不重跑**。无论新库还是旧库，均要执行下一步 Alembic。不要为“重新初始化”直接删除 volume；已有数据升级、备份与失败恢复见 [MySQL 迁移手册](docs/runbooks/mysql-migrations.md)。

## 2. 安装后端、迁移数据库

新机器示例（Windows PowerShell；已有 `mkg` 可跳过创建，直接激活）：

```powershell
conda create -n mkg python=3.10 -y
conda activate mkg
python -B -m pip install -r backend/requirements.txt
cd backend
python -B -m alembic -c alembic.ini heads
python -B -m alembic -c alembic.ini upgrade head
python -B -m alembic -c alembic.ini current
```

`current` 应与本次检出源码的 `heads` 相同；`current=head` 只证明已应用**当前检出中存在**的迁移，不能证明尚未纳入该版本的 `0006`/`0007` 工作台扩展已经可用。复现相应扩展前，先核对克隆所得版本同时包含迁移文件及配套后端代码。不要用 `stamp head` 跳过实际 DDL。既有库必须先停 Flask/worker/Beat、备份并演练恢复，不能照新库步骤在线升级；完整流程见上述迁移手册。PowerShell 找不到 `conda` 时，本机可用 `D:\Miniconda\miniconda3\envs\mkg\python.exe`，但该绝对路径**不是**新机器安装要求。

Neo4j 图谱不是 Compose 自动初始化；仅在需要图谱功能时，在 `backend/` 运行：

```powershell
python -B scripts/neo4j/init_neo4j.py
```

不要用容器内 `cypher-shell -f` 导入当前中文脚本。Milvus 默认使用 `MILVUS_URI=./milvus_data/milvus.db` 的 Lite 文件模式，不需额外启动 Milvus Server；向量/模型依赖会另行下载或加载，基础健康检查不代表它们可用。扫描 PDF 需要独立 OCR/Parse Sidecar，旧 `.doc` 需要 Java 17+ 与 Tika JAR；见 [附件完整性](docs/guides/attachment-completeness.md) 和 [旧版 DOC](docs/guides/legacy-doc-resume.md)。

## 3. 分别启动 Flask、worker、Beat

以下命令都从 `backend/` 目录、同一个已配置的 Python 环境执行，使用三个终端：

```powershell
# 终端 A：HTTP
python -B run.py
# 终端 B：持久任务消费者；Windows 使用 solo，覆盖 Pipeline、Agent 附件和个人图队列
python -B -m celery -A app.services.agent_runtime.media.jobs:celery_app worker --pool=solo --concurrency=1 --queues=cpa.pipeline,cpa.media,cpa.graph --loglevel=INFO
# 终端 C：故障任务的周期恢复扫描
python -B -m celery -A app.services.agent_runtime.media.jobs:celery_app beat --loglevel=INFO
```

`run.py` 只提供 HTTP，不执行 `cpa.pipeline` 中的简历解析或 L3 匹配；若检出版本包含确认后的画像/诊断扩展，该任务同样由 worker 执行。Agent 附件和个人图任务分别走 `cpa.media`、`cpa.graph`。Beat 也不能代替 worker。缺 worker 时已持久保存的任务可能一直等待；不要重复上传来“催促”任务。当前真实岗位发布目录可能为空，L3 无可匹配岗位不等于队列已坏。

## 4. 核验与前端联调

在新终端从仓库根目录：

```powershell
Invoke-RestMethod http://127.0.0.1:5001/health
Invoke-RestMethod http://127.0.0.1:5001/api/pipeline/list
docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
```

严格模式下，未登录用户资源返回 401 是预期行为，不是启动失败。登录/注册依赖 MySQL `user_account` 和 Redis 会话；正式账号、CSRF、Cookie 与管理员开户方式见 [自建账号规格](docs/specs/self-managed-account-auth.md)。前端真实联调请在**独立的前端仓库**复制 `.env.backend-dev.example` 为 `.env.backend-dev`，执行 `npm ci`、`npm run dev:backend`，访问 `http://127.0.0.1:5174`。它以同源 `/api` 代理连接本机后端，不使用公开 Mock 演示账号。新学生账号不会自动继承旧 `student_001` 数据。

运行后端测试（回到仓库根目录）：

```powershell
python -B -m pytest backend/tests
```

部分用例需真实 MySQL/Neo4j/Redis，其他模型、OCR、可信岗位与跨服务最终业务状态仍需单独验收。只验启动可运行 `python -B -m pytest backend/tests/test_app_smoke.py`；不把 HTTP 200 当作异步任务完成。历史 [本地冒烟手册](docs/runbooks/local-smoke-test.md) 的部分前端/身份样例已过时，不能替代本页步骤。

## 5. Compose 生产候选层（尚未完成上线验收）

生产覆盖层现在包含同一 Python 3.10 镜像的 `backend`、`worker`、`beat`，以及必须由运维**显式运行**的 `migrate` profile。worker 消费 `cpa.pipeline`、`cpa.media`、`cpa.graph`，与 HTTP/Beat 共用上传、Milvus、模型缓存和日志卷；Beat 仅保留一个实例。基础层数据库不发布宿主机端口，HTTP 仅映射到 `127.0.0.1:5001`，需要另行配置 HTTPS 反向代理才可对外提供正式账号服务。`/health` 只检查 Flask 自身，不证明依赖或任务可用。

生产 Compose 还要求在未提交的 `.env` 中填写 `CORS_ALLOWED_ORIGINS`，值是**真实前端页面**的 HTTPS Origin（如 `https://app.example.com`；多项用英文逗号分隔，不含路径或末尾斜杠），不得用 `*`。同源 HTTPS 代理可填页面自身 Origin；当前正式账号 Cookie 为 `SameSite=Lax` + `Secure`，建议前后端由同源或至少同站的 HTTPS 入口提供。跨站独立前端即使配置 CORS，也不能据此认为现有 Cookie 登录已可用；需另行设计并验收会话/CSRF 方案。
**用户指定的候选拓扑（尚未配置或验收）**：公网服务器 `101.200.34.104` 仅作为入口/转发中继；应用后端和 MySQL 留在 NeuTronMbook 本机，计划 API 域名为 `api.xingtulabs.top`。DNS、HTTPS 证书和 FRP/其他隧道均未配置；本机休眠、断网或隧道中断会使 API 不可用。MySQL 只允许本机/私网访问，不能映射到公网。上文单机 Compose 生产候选不会自动实现该分离拓扑；这段记录不是部署或网络变更授权，也不代表已上线。

Dev 和生产覆盖层分别使用 `cpa-agent-dev` / `cpa-agent-prod` Compose project 名，命名 volume 相互独立；在 dev 库完成迁移或导入数据，不会自动迁移或复制到生产候选层。

**新空卷的候选启动顺序**（在仓库根目录，先设置强随机 `SECRET_KEY`、真实 `LLM_API_KEY`、`MYSQL_ROOT_PASSWORD`、`NEO4J_PASSWORD` 和上述 `CORS_ALLOWED_ORIGINS`；勿使用模板默认密码或通配 Origin）：

```powershell
$compose = @('-f', 'docker-compose.yml', '-f', 'docker-compose.prod.yml')
docker compose @compose config --quiet
docker compose @compose build backend
docker compose @compose up -d mysql neo4j redis
docker compose @compose ps
# 等待数据库 healthy；不要启动 HTTP/worker 后再迁移
docker compose @compose --profile ops run --rm migrate
docker compose @compose --profile ops run --rm migrate python -B -m alembic -c alembic.ini current
docker compose @compose --profile ops run --rm migrate python -B -m alembic -c alembic.ini heads
# 人工确认 current == heads 后才启动服务
docker compose @compose up -d --no-build backend worker beat
docker compose @compose ps
```

已有 volume **不使用“新空卷”流程直接升级**：先停止新写入，等待 worker 在途任务结束并停 worker/Beat/HTTP，按 [MySQL 迁移手册](docs/runbooks/mysql-migrations.md) 备份 SQL 和上传卷、在隔离库演练恢复，再运行同一个显式 `migrate` 任务。该手册的 `backend/uploads/` 宿主机目录示例**不适用于**此处的 `cpa-agent-prod_backend_uploads` 命名卷；没有经验证的卷导出/恢复方法时，停止在备份前，不升级已有生产 volume。不要用 `stamp head`、删除 volume 或把迁移放在 Flask 启动钩子里。升级失败保持停写，保留现场并按手册恢复。

候选层运行核验：

```powershell
docker compose @compose ps
docker compose @compose logs --tail=80 worker beat
docker compose @compose exec worker python -B -m celery -A app.services.agent_runtime.media.jobs:celery_app inspect active_queues
Invoke-RestMethod http://127.0.0.1:5001/health
```

随后必须经 HTTPS/正式账号走一条有归属的真实简历任务：确认上传任务从 `pending` 到最终状态，核对 `/api/pipeline/trace/<task_id>` 与 owner 范围的工作台成果；再停/启 worker 验证 Beat 对未接单任务的恢复。仅看到 worker/Beat 进程、消息入队、HTTP 200 或页面动画都不算业务验收。构建和静态部署契约测试为 `python -B -m pytest backend/tests/test_compose_deployment.py`；可用真实 MySQL 时以 `CPA_TEST_LIVE_MYSQL=1` 运行 `backend/tests/test_mysql_migrations.py`，该用例只创建/删除自己的临时库。

**仍未具备的生产条件**：HTTPS 反向代理与域名/证书、密钥托管、资源容量和日志策略、数据库及文件卷的自动备份/恢复演练、OCR Sidecar/旧 DOC 组件与真实模型端点、镜像构建及完整业务链路验收。后端 `requirements.txt` 多为版本下界、数据库镜像多为浮动 tag，也尚无完整锁定的可重复构建清单；当前配置不能称为“一键生产上线”。前端有 `package-lock.json`，使用 `npm ci` 而非 `npm install`。

停止开发数据库（**保留数据卷**）：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```
