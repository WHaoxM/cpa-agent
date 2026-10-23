# 生产级镜像改造计划（提案）

> 状态：2026-10-15 计划，**不是上线批准**。目标是 Ubuntu 22.04 单机 Docker；服务器地址、CPU/内存、镜像仓库、域名和 HTTPS 入口尚未提供。生产层不得在这些条件缺失时开放正式账号。本文只规划镜像及其发布链路；数据库、任务、代理与业务验收是联动门禁，不会被“镜像构建成功”替代。

> 进度提示（2026-10-17）：下表“当前基线”是本提案写作时的旧基线，不应覆盖[第三候选的隔离运行证据](../reports/2026-10-17-q1-candidate-runtime-replay.md)。后者已在**未提交、未推送**的本机候选中修正默认入口、固定本机基础 digest/139 项版本，并对另一新恢复 project 完成只读根与正式账号窄链回放；随后生成的 139 项哈希锁与 hash-mode Dockerfile 仍**未进行干净安装或运行回放**。受控无缓存构建、旧卷内部 owner、可信 registry/HTTPS 和完整业务结果仍未验收。

## 1. 当前基线与问题

| 已观察事实 | 上线风险 / 判断 |
|---|---|
| `backend/Dockerfile` 以 `python:3.10-slim` 浮动标签为基底；`requirements.txt` 大量使用版本下界，CPU PyTorch 单独先装 | 同一源码在不同日期可能解析到不同基础层和 Python 依赖；当前没有 Linux/amd64 全量锁定与哈希文件。 |
| `COPY . .` 依赖 `backend/.dockerignore`，而当前工作树有大量未提交/未追踪文件 | 本机刚构建的 `cpa-agent-backend:hardening-20261015` 是开发验证物，不对应已批准的干净提交，不能发布为正式产物。 |
| 镜像已有 `USER 10001:10001`；本机隔离运行验证了 UID 和五处预期写目录 | 只证明新镜像内路径可写；既有 root-owned 卷、应用实际请求写路径、只读根文件系统仍未验收。 |
| Dockerfile 默认 `CMD ["python", "run.py"]`；只有 Compose 覆盖它才使用 Gunicorn | 镜像脱离指定 Compose 时会启动开发入口；必须改成默认失败关闭的生产入口。 |
| 当前单镜像承载 HTTP、Celery worker/Beat、显式 Alembic 迁移与离线快照工具 | 有助于避免代码漂移，但测试包/构建工具不应无条件留在运行层；HTTP 健康检查不能用于 worker/Beat。 |
| 生产 Compose 正在转为预构建 digest 引用和容量必填；MySQL/Neo4j/Redis 也要固定 digest | Compose 静态结构测试通过不代表镜像来源可信、依赖安全、镜像可拉取或业务可恢复。 |
| 当前密钥仍通过 Compose 环境变量注入；应用多个路径直接读取 `os.environ` | 解析后的配置与容器元数据可能暴露密钥；需要先改应用读取边界，再切 Compose secrets。 |
| 旧代码仍有启动/请求时的懒 DDL；应用库账号虽不再是 root，但仍需较高库内权限 | 不能宣称数据库最小权限；镜像改造和迁移收敛要分别验收。 |
| 基础 MySQL 初始化快照写死 `cpa_agent` 库名，生产 Compose 仍继承该挂载 | 不能只改 `MYSQL_DATABASE` 就认为初始化脚本随之切换；P0 须固定本次库名或将初始化源迁为纯 Alembic，并在空卷实测。 |

本地新镜像身份：`sha256:fdb41d980b99e2dff7fc0bcb71e7e99f53fae679473a1d7cbb3fc548f4225f95`，`linux/amd64`，UID 10001；**仅作为一次性对照样本**，不得作为发布 digest 或对应已审查提交的证据。镜像的总大小、解压层大小和实际内存占用须在正式候选生成后分别记录，不用单个 `docker image inspect` 数字代替容量压测。

## 2. 目标产物契约

1. 一个固定的 `linux/amd64` 应用运行镜像，以**干净、固定的 Git commit**、固定基础镜像 digest、固定 Python 3.10 依赖及 wheel 哈希为输入；运行时不 `pip install`、不现场拉取源码。根据服务器架构再决定是否另造 `arm64` 产物。
2. 同一 digest 用于 HTTP、worker、Beat 和一次性迁移；各进程有不同启动命令、健康/就绪判据及资源配额。镜像默认命令必须是生产 WSGI 或明确失败，不能落到 Flask 开发服务器。
3. 最终运行层不包含测试框架、构建缓存、`.env`、业务上传、模型缓存、数据库文件、备份、Git 元数据或真实凭据。需要的模型/OCR 能力以显式依赖和可重复准备步骤声明，不依赖隐蔽运行时下载；若需要单独 Sidecar，另列镜像及最终业务验收。
4. 常驻进程非 root、只写声明的卷或 tmpfs；完成路径审计后启用只读根文件系统、`cap_drop: [ALL]`、`no-new-privileges`、进程/内存/CPU 限额。离线冷备份工具为保留数据库文件 UID/GID 可在短生命周期、无网络、明确命令下用 root，不能把这个例外带到常驻服务。
5. 构建发布的是**同一份经过验证的 registry digest**，附 SBOM 与 provenance；发布记录绑定 commit、锁文件摘要、基础层 digest、镜像 digest、配置版本、测试与例外审批。上线不在服务器重建，不以可覆盖 tag 作为部署身份。

## 3. 实施顺序与验收

| 阶段 | 改造工作 | 完成证据 / 阻断条件 |
|---|---|---|
| P0 清点与来源冻结 | 盘点 backend 运行时 import、PyTorch/Milvus/OCR 等平台轮子、模型下载、所有写路径与 HTTP/worker/Beat/迁移入口；划分运行依赖与测试依赖；从干净 commit 建立候选。 | 保存 commit、工作树状态、目标平台、当前镜像包清单/大小和最短业务路径。工作树脏、来源不明即停发。 |
| P1 可重复构建 | 将 Python 依赖拆为直接约束与面向 `linux/amd64`/Python 3.10 的完整锁定；固定基础镜像及 CPU torch wheel digest/hash；优先用经验证的锁定工具/`pip --require-hashes`，不手写平行解析器。多阶段构建 wheelhouse/运行层，并收紧 `.dockerignore`。 | 在干净构建器上无缓存构建，`pip check`、关键模块 import、镜像文件清单及依赖锁一致；基础层或依赖变化产生新候选，不静默复用旧批准。 |
| P2 运行时收敛 | 默认入口改为生产 WSGI；HTTP、worker、Beat、migrate 命令/健康定义分离；应用仅写列出的上传、Milvus Lite、缓存、日志及必要 tmpfs。解决旧卷 UID 迁移，不在服务启动时全盘 `chown`。 | 同一 digest 的四进程隔离启动；非 root、只读根文件系统和必要卷读写测试；SIGTERM 后 worker 在途任务与 Beat 单实例行为实测。worker/Beat 不能以 HTTP `/health` 代替。 |
| P3 凭据与权限 | 先建立应用 `_FILE`/secret-file 读取适配并覆盖所有直接读取环境变量的调用，再将应用/数据库凭据迁到最小授权的 Compose secrets；迁移进程独享高权限凭据。逐个移除运行时懒 DDL，回归真实 HTTP 与 Celery 链路后收窄应用库权限。 | `docker inspect`/解析配置不含密钥值，镜像 history/层不含密钥；错误日志不回显秘密；应用账号无法执行未授权 DDL，迁移仍可显式执行。未完成前不得称“最小权限生产配置”。 |
| P4 供应链发布 | 在受控构建环境构建并推送到确定的镜像仓库，生成 SBOM/provenance，扫描漏洞与明文秘密；对可修复的严重/高危项失败或记录有期限例外。签名/验证策略与镜像仓库确定后落地，记录不可变 digest。 | 从 registry **按 digest 重新拉取**相同候选，核对平台、来源 commit、锁文件与 attestation；Compose 四进程引用一致；无现场 `build`。 |
| P5 隔离与上线验收 | Ubuntu 22.04 上使用全新 Compose project/卷，依次拉镜像、启动依赖、显式迁移、启动 HTTP/worker/Beat；复核资源、日志、健康、任务状态、冷备份和异项目恢复。最后经正式 HTTPS/账号走 owner-scoped 简历任务到权威结果。 | 静态检查、容器运行、真实数据库/队列、恢复后的业务读取和对外 HTTPS 旅程均有记录；只通过测试/`/health`/镜像扫描不准上线。 |

P1/P2 可在本机先做；P4 需要镜像仓库及发布身份，P5 需要服务器 CPU/内存、SSH、域名/证书方案和真实外部依赖。磁盘“至少 200 GB”只说明可用空间量级，不能据此确定内存、CPU、备份保留周期或模型容量。各阶段只推进满足前置条件的工作，不用缺省值假装已完成定容。

## 4. 发布、回滚与职责边界

- 发布前：冻结候选 digest；核对解析后的 Compose 无数据库公网映射、所有镜像 digest、容量/日志上限、密钥来源和当前 Alembic head；做一次恢复演练并保留上一个可用 digest。生产环境的 TLS、鉴权、回调/上传与任务结果必须另验。
- 发布中：维护窗口停写并等 worker 在途任务；冷备份；只运行一次迁移；校验 schema head；再启动服务。MySQL DDL 可能非事务性，失败后不盲目 `stamp head` 或自动回滚。
- 失败时：若仅代码失败且 schema 兼容，恢复旧 digest 并回验；若数据/结构不兼容，保持停写，在**新的隔离项目**验证冷备份恢复或写向前修复迁移。不要对现有业务卷执行 `down -v`。
- 任何镜像构建、锁文件、基础层、secret 读取方式、迁移或 Compose 环境改变，都产生新的候选和相应专项复验，不沿用旧镜像的验收结论。

## 5. 立即可做与待用户提供

- 立即可做：P0/P1 的源码/依赖/写路径清单、锁定方案验证、Dockerfile 多阶段原型、默认入口修复及镜像内非 root/只读测试；继续保持本地隔离，不触碰现有业务卷。
- 待提供：服务器 CPU/内存/架构、SSH 地址与发布权限、镜像仓库、域名或受信任 HTTPS 入口、模型/OCR 服务选型、备份目标与保留要求。没有这些只能形成生产**候选**，不能宣称正式上线。

## 参考标准（实现时按版本复核）

- [Docker 构建最佳实践](https://docs.docker.com/build/building/best-practices/)：基础层、构建上下文、多阶段和临时容器。
- [pip 安全安装与哈希模式](https://pip.pypa.io/en/stable/topics/secure-installs/)：锁定分发文件并校验哈希。
- [Docker Build attestations](https://docs.docker.com/build/metadata/attestations/)：SBOM、provenance 与 registry 保存边界。
- [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)：逐服务文件挂载与环境变量泄露风险。
- [Docker Build secrets](https://docs.docker.com/build/building/secrets/)：构建期凭据不进入 ARG/ENV 或镜像层。
