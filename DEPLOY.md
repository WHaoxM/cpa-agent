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

`current` 应与本次检出源码的 `heads` 相同；`current=head` 只证明已应用**当前检出中存在**的迁移，不能证明尚未纳入该版本的 `0006`/`0007` 工作台扩展已经可用。复现相应扩展前，先核对克隆所得版本同时包含迁移文件及配套后端代码。不要用 `stamp head` 跳过实际 DDL。既有库必须先停 Flask/worker/Beat、备份并演练恢复，不能照新库步骤在线升级；完整流程见上述迁移手册。PowerShell 找不到 `conda` 时，使用 Python 3.10 环境中 `python.exe` 的完整路径；本机安装位置因机器而异，新机器无需沿用。

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

For deployments that keep code, runtime config, and data in separate directories, set `CPA_UPLOAD_FOLDER` and `CPA_LOG_DIR` in the private runtime environment file. These override the development defaults `backend/uploads` and `backend/logs`; keep uploads in the backed-up data directory and logs in a separate writable directory. Do not commit the runtime environment file.

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

## 5. Compose 生产候选层（暂不可正式上线）

**此节是当前状态和发布门禁，不是可复制的生产启动步骤。** 镜像改造的阶段、产物契约与停止条件见 [生产级镜像改造计划](docs/specs/production-image-hardening.md)。目标为 Ubuntu 22.04 单机 Docker；目前没有已批准的镜像仓库 digest、服务器 CPU/内存、域名/HTTPS 入口、真实恢复与业务验收，因此不能对外开放正式账号。此前 `docker compose build backend`、使用浮动 tag 现场构建、以及访问宿主机固定 `127.0.0.1:5001` 的候选命令已失效，不得作为上线手册使用。

**用户指定的候选拓扑（尚未配置或验收）**：公网服务器 `101.200.34.104` 仅作为入口/转发中继；应用后端和 MySQL 留在 NeuTronMbook 本机，计划 API 域名为 `api.xingtulabs.top`。DNS、HTTPS 证书和 FRP/其他隧道均未配置；本机休眠、断网或隧道中断会使 API 不可用。MySQL 只允许本机/私网访问，不能映射到公网。上文单机 Compose 生产候选不会自动实现该分离拓扑；这段记录不是部署或网络变更授权，也不代表已上线。

当前覆盖层将同一 Python 3.10 应用镜像用于 Gunicorn `backend`、`worker`、`beat` 和运维显式触发的 `migrate` profile。worker 消费 `cpa.pipeline`、`cpa.media`、`cpa.graph`；Beat 必须保持单实例。Gunicorn 启动前拒绝 demo/debug、不安全 Cookie、弱/默认密钥、通配或非 HTTPS 远端 Origin，以及未到当前镜像 Alembic head 的 MySQL；它只读版本账本，**不自动迁移**。默认不发布 HTTP、MySQL、Neo4j、Redis 的宿主机端口；容器内的 `backend:5001` 不是公网入口。若将来使用宿主机 HTTPS 代理，只能显式叠加 `docker-compose.prod.host-proxy.yml` 并选择空闲 loopback 端口；若使用容器代理，应加入 Compose 网络直连 `backend:5001`。`/health` 只检查 Flask 自身，不证明依赖或任务可用。

生产候选要求单独的、未提交且仅所有者可读的环境文件，不得直接复制带开发默认密码的 `.env.example`。Compose 必填 `CPA_COMPOSE_PROJECT_NAME`，应用/MySQL/Neo4j/Redis 的 registry `@sha256:` 镜像引用、`MYSQL_ROOT_PASSWORD`、独立的 `MYSQL_APP_PASSWORD`、`NEO4J_PASSWORD`、强随机 `SECRET_KEY`、真实 `LLM_API_KEY`、`CORS_ALLOWED_ORIGINS`，以及七个服务（MySQL、Neo4j、Redis、HTTP、worker、Beat、migrate）的 `CPA_<服务>_MEM_LIMIT` 和 `CPA_<服务>_CPUS`。限制值需按服务器 CPU/内存和实测峰值确定，不从“磁盘 200 GB”猜测。`CORS_ALLOWED_ORIGINS` 必须是**真实前端页面**的 HTTPS Origin（如 `https://app.example.com`；多项英文逗号分隔、无路径/末尾斜杠），不得用 `*`。正式账号 Cookie 为 `SameSite=Lax` + `Secure`，优先同源或同站 HTTPS；跨站前端不能只靠 CORS 接通会话/CSRF。

Dev project 固定为 `cpa-agent-dev`；生产 project 由 `CPA_COMPOSE_PROJECT_NAME` 显式选择。两者命名卷独立；dev 的迁移或数据不会自动复制到生产。已有生产卷若源自 root 运行镜像，还需单独做 UID/GID 兼容演练，不能直接换非 root 镜像。

**将来的新空卷顺序**：从干净 commit 构建并发布带 SBOM/provenance 的镜像 → 按 digest 拉取、核对来源和平台 → 用 `backend/scripts/ops/release_preflight.py` 对解析后的 Compose 做静态门禁（只输出不含秘密的检查项）→ 在隔离 project 启动依赖并等健康 → 运维只运行一次 `migrate`、比较 Alembic `current/heads` → 启动 HTTP/worker/Beat → 经 HTTPS 和正式账号验证最终业务状态。当前缺前置条件，**不提供可直接复制的 `up` 命令**；`preflight` PASS 也只代表静态配置合格，绝不是上线批准。

**已有 volume 不使用“新空卷”流程直接升级。** 先阻止新请求、等待 worker 在途任务结束，再停掉所有写入进程和数据库；冷快照工具 `backend/scripts/ops/volume_snapshot.py` 必须使用实际 `--project` 和同一份生产 `--env-file`，备份目录要在受保护、有足够空间的位置。恢复永远指向全新 project。其离线 helper 为保留数据库文件 UID/GID 会在 `--network=none` 的一次性容器中以 root 运行；常驻应用仍为 UID 10001。备份含用户数据，须校验目录权限、离机备份/加密和保留策略。当前不提供“升级现有生产卷”的直接命令。

工具只备份 MySQL、Neo4j、Redis、上传文件与 Milvus Lite 的 **5 个必需命名卷**，拒绝仍被运行中容器占用的源卷，逐文件摘要并在新项目的空卷中校验恢复；模型缓存和日志不在快照内。恢复演练还须用隔离项目核对数据库关键行/触发器、上传文件和实际业务读取；仅通过哈希或 `ps` 不能证明业务恢复。绝不对源项目执行 `down -v`，也不要把恢复目标项目与源项目同名。**当前工具仅通过本地目录往返/拒绝性测试，尚未完成真实命名卷恢复演练；在该演练完成前不得升级已有生产 volume。** 非 Compose 宿主机目录的 SQL+文件备份流程仍见 [MySQL 迁移手册](docs/runbooks/mysql-migrations.md)，不能拿它替代命名卷快照。演练通过并确认维护窗口后，才在源项目显式运行 `migrate`、比较 `current/heads`、启动 HTTP/worker/Beat。迁移失败保持停写、保留失败现场，不用 `stamp head`、删除卷或假定 MySQL DDL 已回滚。

候选层至少核对：实际拉取镜像 digest、各服务 `ps`/健康状态和有界日志、容器内 HTTP `/health`、worker 的生产队列注册、Beat 单实例、任务持久状态及恢复。默认没有宿主机 HTTP 端口，不能再用 `http://127.0.0.1:5001/health` 作为宿主机验收地址。

随后必须经 HTTPS/正式账号走一条有归属的真实简历任务：确认上传任务从 `pending` 到最终状态，核对 `/api/pipeline/trace/<task_id>` 与 owner 范围的工作台成果；再停/启 worker 验证 Beat 对未接单任务的恢复。仅看到 worker/Beat 进程、消息入队、HTTP 200 或页面动画都不算业务验收。静态部署、WSGI 配置门禁与冷快照单测为 `python -B -m pytest backend/tests/test_compose_deployment.py backend/tests/test_release_preflight.py backend/tests/test_production_wsgi.py backend/tests/test_volume_archive.py backend/tests/test_volume_snapshot.py`；可用真实 MySQL 时以 `CPA_TEST_LIVE_MYSQL=1` 运行 `backend/tests/test_mysql_migrations.py`。Celery/Beat 的隔离真服务测试见 [迁移手册](docs/runbooks/mysql-migrations.md)。

**仍未具备的生产条件**：正式 HTTPS 入口/域名与服务器访问；服务器 CPU/内存定容；已发布、可拉取且可验证来源的镜像 digest；完整依赖锁定；Compose secrets 与运行时懒 DDL 收敛；真实 Docker 命名卷恢复演练；OCR Sidecar/旧 DOC 组件、真实模型端点和完整业务链路验收。新镜像非 root 与 Compose 有界日志属于局部改善，不抵消这些阻断项。前端有 `package-lock.json`，使用 `npm ci` 而非 `npm install`。

停止开发数据库（**保留数据卷**）：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```
