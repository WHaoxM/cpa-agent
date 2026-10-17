# Q1 干净候选镜像与隔离运行链路回放（2026-10-17）

**结论：**在已演练恢复的全新 Compose project 上，21 个部署候选文件构成的本地干净来源镜像完成了显式 MySQL 迁移、真实数据库/Redis/Neo4j 启动、Gunicorn HTTP、Celery Beat→Redis→worker，以及经本机 HTTPS 代理的正式账号和 owner-bound 收藏写入/数据库回读。它是**隔离运行证据，不是发布批准**：依赖和基础镜像尚未锁定到可重建输入，本机自签证书不代表受信任公网入口，真实模型/OCR/完整学生业务链未验，镜像未推送 registry。

## 来源、产物与隔离边界

- 主仓基线 `d4e1cd2` 上，以独立 Git index **仅选 21 个部署文件**生成本地候选提交 `d6f2379f35eca2158f0c0b2d9a430072e7ba084a`，tree `eb0522934793dd598b7e6a162b61bf3f715e2a26`；本地 detached worktree 为被忽略的 `output/q1-source-20261017/`。`git diff --cached --check` 通过；主仓 index 始终为空。候选提交**未推送、未并入主分支**，22 个未审 Agent 候选不在该干净来源内。
- 用该 worktree 的 `backend/Dockerfile` 和 `backend/` 上下文构建本机镜像 `cpa-q1-app@sha256:c156e0fbe6d9b1e4579bdbb6bfb50522bd6d5b6de1e93bfacba02c0af6d0f82a`；镜像标签记录上述 commit/tree，运行用户为 `10001:10001`，`production_wsgi.py` 存在，未含抽查的未审 Agent 模块。镜像内 `volume_archive.py` 与干净 worktree 文件 SHA-256 同为 `5ee6facb0f891245e2da9d6866b22ba6c5b3008a319609ea9760b035356f0067`。主工作树同文件的 `a583f...` 是不同字节（Git 换行规范化），不可混称同一字节产物。
- Python 基础镜像本机解析为 `python@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d`，但 Dockerfile 仍写浮动 `python:3.10-slim`；`requirements.txt` 多数是下界而非 lock。构建时 `pip check` 无冲突，镜像实装 Flask 3.1.3、Gunicorn 23.0.0、Celery 5.6.3、LangGraph 1.2.16、pymilvus 3.0.2、milvus-lite 3.2.2、transformers 5.20.1、openai 3.33.0。该 digest 可重用现有本机镜像，**不能保证今后按同一源码重新构建出同一运行依赖**。构建日志还提示 `pymilvus 3.0.2` 没有 `milvus_lite` extra，另行安装的 `milvus-lite` 实际存在；需清理依赖声明并做重建回归。镜像默认 `CMD` 仍是开发入口 `python run.py`，本轮 Compose 显式覆盖为 Gunicorn；独立运行镜像不能当生产服务。
- 复用[此前冷卷恢复](2026-10-17-q1-isolated-volume-recovery.md)的目标 project `cpaq1restore20261017b`，只在其已恢复卷上继续隔离演练；原 `cpa-agent_*` 与其他历史卷未碰。插值文件 `output/q1-isolated-20261017.env`、本机自签代理配置/证书和探针 `output/q1-proxy-20261017/` 均被忽略且不提交。应用镜像与代理镜像均为本机 digest，未做 registry 来源/签名验收。

## 实际链路与结果

1. 使用干净 worktree 的基础/生产 Compose 双文件和上述 app digest，只启动恢复 project 的 `mysql neo4j redis`：三服务均为 `healthy`。镜像内 Alembic `heads` 为 `0007_student_portrait_snapshot`；迁移前 `current` 为空，显式 `migrate` 退出 0，迁移后 `current` 精确为该 head。没有把 `stamp` 当迁移，也未对原业务库运行。
2. 同一 digest 启动 `backend worker beat`。Gunicorn 23.0.0 双 worker 在 `/health` 返回 200，MySQL/Neo4j/Redis 保持 healthy；worker 与 Beat 实际连接 `redis://redis:6379/0`，`celery inspect ping` 返回 1 个在线节点，worker 监听 `cpa.pipeline`、`cpa.media`、`cpa.graph`。Beat 至少两轮发送 5 个计划任务，worker 日志有对应收到与成功处理，包括诊断重放、岗位源刷新、匹配/画像/简历重放；本次待重放队列为空，**不能据此宣称完整业务成功**。`docker compose up --wait` 对关闭健康检查的 worker 返回 1（`has no healthcheck configured`），虽然进程持续运行且实际处理消息；运维验收要分服务判断，不能把此返回码误当所有服务失败或成功。
3. 本机 `nginx@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10` 只绑定 `127.0.0.1:18443`，容器网络内代理到 `backend:5001`，TLS 证书为一天有效的自签 `localhost`。HTTP 入口没有对外发布。该代理的 HTTPS `/health` 返回 200；探针为自签证书关闭了证书链验证，**只证明本机 TLS/安全 Cookie 路径，未证明受信任域名或正式证书**。
4. 被忽略的 `output/q1-proxy-20261017/probe_https_owner.py` 在上述 HTTPS 入口以两套独立 Cookie 会话完成 CSRF 获取、学生注册（伪造 `role=admin` 和 ID 未被采信）、正式登录、CSRF 保护、owner 收藏岗位写入、双账号列表隔离和删除。无 CSRF 写入为 400，直查隔离 MySQL `saved_job` 无行；带 CSRF 写入 200，**独立直查 MySQL** 恰有一行且 `user_id` 等于登录 owner，不等于请求体中伪造的另一账号；两账号数据库 `role` 均为 `student`。owner 的列表可见、他人的列表不可见；删除 200 后再次直查 MySQL 为零行。探针输出末尾为 `PASS`，本机一次性账号行保留在该隔离卷中，不属于正式用户数据。
5. 验证后停止并移除本机临时代理容器，停止恢复 project 的六个服务；**九个隔离卷及容器保留**供复核，未执行 `down -v`。源 project 亦保持停止。主仓 staging 仍为空；前端仓未参与此镜像。

## 最短重放与停止项

在本机现有镜像、证书尚有效且隔离卷保留时，设置仅本次 shell 的 `CPA_COMPOSE_PROJECT_NAME=cpaq1restore20261017b` 和 `CPA_BACKEND_IMAGE=cpa-q1-app@sha256:c156e0fbe6d9b1e4579bdbb6bfb50522bd6d5b6de1e93bfacba02c0af6d0f82a`，以 `output/q1-isolated-20261017.env` 配合 `output/q1-source-20261017/docker-compose.yml`、`output/q1-source-20261017/docker-compose.prod.yml` 启动三数据库及后端/worker/Beat；先核对 Alembic `current=head`。用 `output/q1-proxy-20261017/default.conf` 和该目录自签证书起只绑定本机的代理，然后用 `mkg` Python 运行 `output/q1-proxy-20261017/probe_https_owner.py`；它每次创建新随机账号/岗位并在结束前删除该岗位，数据库直查在脚本中执行。**不要**对已有目标卷重跑 `restore`，也不要把一次性插值文件用于原业务 project。

本机 PowerShell 的关键命令（从仓库根目录，使用被忽略的本轮一次性 env 文件；`--wait` 不适用于无 healthcheck 的 worker/Beat）：

```powershell
$env:CPA_COMPOSE_PROJECT_NAME = 'cpaq1restore20261017b'
$env:CPA_BACKEND_IMAGE = 'cpa-q1-app@sha256:c156e0fbe6d9b1e4579bdbb6bfb50522bd6d5b6de1e93bfacba02c0af6d0f82a'
$files = @('-f', 'output/q1-source-20261017/docker-compose.yml',
           '-f', 'output/q1-source-20261017/docker-compose.prod.yml')
docker compose --env-file output/q1-isolated-20261017.env @files up -d --no-build --pull never mysql neo4j redis
docker compose --env-file output/q1-isolated-20261017.env @files run --rm --no-deps --entrypoint python migrate -B -m alembic -c alembic.ini current
# 仅在独立恢复副本且完成停写/快照确认后，显式运行迁移：
docker compose --env-file output/q1-isolated-20261017.env @files run --rm --no-deps migrate
docker compose --env-file output/q1-isolated-20261017.env @files run --rm --no-deps --entrypoint python migrate -B -m alembic -c alembic.ini current
docker compose --env-file output/q1-isolated-20261017.env @files up -d --no-build --pull never backend worker beat
docker exec cpaq1restore20261017b-worker-1 python -B -m celery -A app.services.agent_runtime.media.jobs:celery_app inspect ping --timeout=5
```

代理在本轮已停删，重放时需以 `output/q1-proxy-20261017/default.conf` 和同目录 `localhost.crt`/`localhost.key` 重新创建同名临时 Nginx 容器；证书 2026-10-18 08:15 UTC 后过期，必须重新生成**仅供本机测试**的证书。探针命令为 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B output/q1-proxy-20261017/probe_https_owner.py`。完成后停止代理与该恢复 project 的六个服务，保留隔离卷。`output/` 下这些本机文件不随 Git 分发；外部重放须先将候选源码和受信任发布身份正式定稿。

下一发布门槛：锁定基础镜像和完整依赖并从干净来源重建/比对；将镜像默认入口改为 fail-closed 的生产入口或明确限制其用途；从可信 registry 核验 digest/签名及分发；在目标服务器与可信 HTTPS 域名/证书、真实密钥管理和外部依赖齐备后重放迁移/恢复与正式账号的受影响业务成果。真实模型、OCR、Milvus、完整 L0→L2→L3/L4、跨服务故障恢复及公网入口尚未通过；这些不能由本轮 `/health`、收藏写入或 Beat 空队列处理推断。

## 续验：镜像默认入口收窄（同日，仍仅本机）

第一候选镜像的默认 `CMD` 仍是开发服务器，这是经 `docker image inspect` 确认、且会影响脱离 Compose 的实际缺口。先给 `backend/tests/test_compose_deployment.py` 加默认入口回归，原实现 **1 failed**；再仅将 `backend/Dockerfile` 的默认 `CMD` 改为 `gunicorn ... production_wsgi:create_production_app()`，原有五个部署专项现为 **46 passed in 7.72s**。这两处代码仍在主仓未提交的部署候选中，未单独推送。

再次从 21 个白名单文件生成**新的**本地候选提交 `3c4a6d22e75fb9434086e51678c3b3de9bd78ec5`、tree `87d0aa1c7843f506735e44eb762d0815f639b280`，干净 worktree 为 `output/q1-source-20261017b/`，主仓 index 为 0。增量构建的新镜像为 `cpa-q1-app@sha256:573f93963095efef63087cb124c39376797199996ae62236b2ce8f9c0c85b8a1`；基础层和依赖安装层命中本地缓存，**没有**证明无缓存重建或供应链锁定。容器不提供任何生产配置、且网络关闭时直接用镜像默认命令启动，Gunicorn worker 因 `production_wsgi` 配置门禁退出，容器退出码 1；不再落到 `run.py`。随后以本机覆盖文件 `output/q1-proxy-20261017/default-image-command.yml` 将 Compose 对 HTTP 的 `command` 设为 `null`，在同一隔离恢复 project 使用镜像**自身默认命令**：容器 `Config.Cmd` 与新 Gunicorn CMD 完全一致、HTTP health 为 200，worker inspect ping 返回在线，Beat 实际投递且 worker 处理了计划任务。

第一次复验账号路径在第二次注册返回 **429**：沿用同一隔离 Redis，上一镜像的两次注册与本次第一次注册已填满 `3 per hour`，这是限流生效而非新镜像故障。核对只属于该隔离探针的 `LIMITS:LIMITER/cpa-account-auth/cpa-account-auth:172.31.0.8/auth.register/3/1/hour` 键存在且剩余 TTL 3007 秒后，仅删除该**精确测试键**（未 `FLUSHDB`、未触碰其他 project），重放同一探针为 `PASS`：两次注册/登录、无 CSRF 400、owner 写入 200、MySQL 行归属、另一账号不可见和删除后 MySQL 零行均成立。该限流计数重置是**隔离测试复位**，不是部署操作；外部重放应使用新的隔离 Redis/账号，不能在业务环境重置限流。新镜像与旧镜像、两个本地候选 commit 都保留，不混称同一发布物。代理已停删、六个隔离服务已停止、卷保留。

默认入口这一窄缺口已修，但本报告前述**基础镜像标签/完整依赖未锁定、registry/可信证书/完整业务链缺口仍在**；新镜像不能被称为可重建的正式发布镜像。上面的“最短重放”命令针对第一候选；复验新镜像须把路径改为 `output/q1-source-20261017b/`、镜像 digest 改为 `sha256:573f...`，并加 `default-image-command.yml` 覆盖文件以验证镜像自身默认入口。

## 续验：固定本机基础层/版本并从再次恢复的新卷回放（同日）

为降低再次解析依赖的漂移，第三候选在 `backend/Dockerfile` 固定本机已验证的 Python 3.10 基础 digest，在先前成功镜像的 `pip freeze` 上形成 `backend/constraints-linux-amd64-py310.txt`（139 项 `==` 版本），两个 pip 安装步骤都受约束；`pymilvus` 的过时 `milvus_lite` extra 去除，独立 `milvus-lite` 依赖保留。先加静态门禁，原 Dockerfile 运行 **2 failed**；修改后六个部署/迁移专项为 **49 passed, 2 skipped in 11.36s**，跳过的是需 opt-in 的真库迁移测试。约束文件是**已成功环境的版本快照，不是含 wheel 哈希的可移植 lock**；仍须补轮子哈希、索引身份和受控无缓存重建。

本地白名单从 21 增至 **22 个部署文件**（新增上述 constraints；依然不含 22 个 Agent 实验模块），独立 index 生成仅本机候选提交 `c45819c3b04834cb9404c7ff628be1ab8e216324`、tree `ab04fd1a4dc2015f202fc9a7a670cf2f6f8c3864`，干净 worktree 在 `output/q1-source-20261017c/`；主仓 index 保持空。候选镜像 `cpa-q1-app@sha256:44e9931020ad8ee263de346e0d5e09fe586073d61a6fd8352299708a6bc7c658` 使用固定基础 digest，内有 139 项安装版本与 constraints 逐行 **0 差异**，`pip check` 无损坏依赖，UID/GID 仍为 `10001:10001`，未审 Agent 文件不在镜像内。Docker 层缓存未完全关闭；另起的 `--no-cache --pull=false --progress=quiet` 构建运行约 15 分钟后由操作者中断，静默模式没有提供足够阶段诊断，**既不记为无缓存构建成功，也不据此判断失败原因**，不宣称镜像字节可重现。

为避免上一目标卷的账号/限流残留，本次没有重置已有 Redis：对先前冷快照重新 `verify` 后，恢复到**从未存在过的**新 project `cpaq1restore20261017c`，只新建它自己的五个恢复卷，后来启动服务时再创建四个空运行卷。真实 MySQL、Redis、Neo4j 和 uploads/Milvus 两个文件卷均从该新卷回读出原 `q1-20261017a` 标记。迁移前 Alembic `current` 为空、镜像 head 为 `0007_student_portrait_snapshot`；显式迁移退出 0，迁移后 `current` 与 head 一致。候选 `release_preflight.py` 对解析后 Compose 返回 `PASS`，明确只代表静态入场。

同一第三候选 digest 以覆盖 `command: null` 运行镜像**自身默认** Gunicorn 入口；HTTP healthy、MySQL/Neo4j/Redis healthy，worker inspect ping 在线。隔离 HTTPS 自签代理上，两套新注册的正式学生账号从 CSRF、登录到收藏岗位写入/删除完成一次全新回放：无 CSRF 写入 400 且 MySQL 无行；有 CSRF 写入 200，独立直查 MySQL 的行归 owner、伪造的另一账号 ID/管理员角色无效；另一账号列表不可见，删除后 MySQL 归零。探针输出 `PASS`。Beat 实际投递五个计划任务，worker 日志对应 **5 received / 5 succeeded**，其中重放队列为空，不代表完整 L0/L2/L3/L4 成功。业务请求后五类恢复标记再次回读仍正确。验证后代理停删、`cpaq1restore20261017c` 六个服务停止，九个卷/容器保留，没有 `down -v` 或改动原有业务卷。

第三候选是目前**最强本机回放身份**，前两候选仅供差异/失败追溯，不应作为后续首选重放对象。外部发行仍停在 wheel 哈希/无缓存构建、registry 拉取与签名、可信 HTTPS 域名/证书、凭据最小化、资源/只读根路径和完整学生业务成果；本地 Git WIP commit 与 image digest 不会自动推送。复核时先用 `output/q1-source-20261017c/` 和上述第三候选 digest，并选择**另一个新 Compose project** 从快照恢复，避免沿用本轮账号/限流状态；本机探针可设置 `Q1_BACKEND_CONTAINER` 指向新 backend 容器。

## 续验：受限根文件系统、恢复卷所有权与再一次全新回放（同日）

第三候选没有改动，继续使用**同一个** `sha256:44e993...` 镜像。先在恢复 project `cpaq1restore20261017c` 上叠加仅本机、被忽略的 `output/q1-proxy-20261017/readonly-app-overlay.yml`：HTTP、worker、Beat 的根文件系统只读，`/tmp` 为 64 MiB tmpfs，`cap_drop: [ALL]`、`no-new-privileges`。三进程确实启动，HTTP `/health` 为 200，worker `inspect ping` 在线，Beat 的任务仍被 worker 接收与处理。但一个逐目录写入探针在 `/app/uploads` **PermissionError 13**，不能从“进程健康”推断上传可用。`docker inspect` 和 `stat` 证明恢复的 uploads/Milvus 卷根目录是 `0:0/0755`；logs/两个缓存卷根目录则是 `10001:10001/0755`。恢复器按设计保留原数值所有权，镜像以 `10001:10001` 运行，冲突发生在真实恢复状态，而不是只读根本身。

在**仅此隔离 project** 中先停止三个应用进程，核对两卷的 Compose project/volume 标签、无运行容器占用，以及原 WSL 冷快照 `snapshot.json` SHA-256 仍为 `f78ccc76d605f7a9a0bf3d9c54c4f1a1199cf62518f08d39dd52b816f40741d4` 且 `volume_snapshot.py verify` 退出 0。然后用同一固定 app 镜像作为无网络、只读根、短生命周期的 root helper，仅保留 `CHOWN` capability，并**只将两个卷的顶层目录**从预期 `0:0` 改为 `10001:10001`；没有递归改文件、没有碰数据库卷或任何原业务卷。复启后，`output/q1-proxy-20261017/probe_readonly_mounts.py` 在 uploads、logs、Milvus、两个缓存及 `/tmp` 六处写入/回读/删除均通过；向 `/app` 与 `/home/cpa` 写入均为 `EROFS 30`。文件卷旧 marker 仍可读取且文件自身继续属于 root，证明此项只修复了**本轮空/浅层测试卷的顶层写入**；若真实旧卷内有 root-owned 子目录、Milvus 数据库/WAL 或待修改文件，须逐项盘点并制订单独维护迁移，不能把本轮顶层 `chown` 当通用递归方案。两个探针只在被忽略的本机 `output/`，不随 Git 分发。

为消除同一 Redis 的限流和旧账号状态，再从已核验的**原冷快照**恢复到事先确认不存在的新 project `cpaq1restore20261017d`，仅创建其五个恢复卷；在应用进程启动前，以同样的标签/停机/预期 owner 门禁对 uploads/Milvus 两个新卷做相同的顶层所有权调整。基础三服务健康；只读根/零 capability/no-new-privileges 的迁移容器显式执行迁移，迁移前 `alembic current` 无版本，迁移后为 `0007_student_portrait_snapshot`；同镜像的 HTTP、worker、Beat 亦按上述约束启动。三进程 `docker inspect` 均显示 `ReadonlyRootfs=true`、`CapDrop=[ALL]`、`SecurityOpt=[no-new-privileges:true]`；HTTP healthy/200，worker 在线。六处可写目录的创建/回读/删除及两处根文件系统拒写再次 `PASS`。

本机 Nginx 只绑定 `127.0.0.1:18443`，用仍有效的**自签** localhost 证书代理到新 project；两套全新正式学生 Cookie 会话重新完成 CSRF、注册/登录、无 CSRF 写入 400 与独立 MySQL 零行、有效写入 200 与 MySQL owner 行、他人不可见、删除后 MySQL 零行，探针 `PASS`。随后单独从同一运行容器回读 MySQL、Redis、Neo4j、uploads 与 Milvus 五个冷快照 marker，全部 `PASS`。临时代理已停删，新 project 六服务已停止，九卷和容器保留；原业务卷及前三个隔离 project 未被清理。这证明**特定第三候选 + 此次顶层所有权准备 + 本机只读根安全覆盖**能承载上述窄业务链，不证明根目录以外的全量上传/模型/OCR/Milvus 真实写入。

关键的隔离重放条件：`CPA_COMPOSE_PROJECT_NAME=cpaq1restore20261017d`，`CPA_BACKEND_IMAGE=cpa-q1-app@sha256:44e9931020ad8ee263de346e0d5e09fe586073d61a6fd8352299708a6bc7c658`，Compose 文件依次为干净 `output/q1-source-20261017c/{docker-compose.yml,docker-compose.prod.yml}`、被忽略的 `output/q1-proxy-20261017/{default-image-command.yml,readonly-app-overlay.yml}`，插值使用本轮一次性 `output/q1-isolated-20261017.env`。**不要**对已经存在的 `d` 卷再执行 `restore`；新验收应换全新 project 并先核验目标卷不存在。顶层所有权调整只在停机、已验证快照、精确 project 卷标签、根目录原 owner 为 `0:0` 的条件下允许；操作容器是 `--network none --read-only --user 0:0 --cap-drop ALL --cap-add CHOWN --security-opt no-new-privileges`，只挂载单一隔离卷并执行 `os.chown('/target',10001,10001,follow_symlinks=False)`。此操作属于隔离卷维护验证，**尚未形成可直接照搬到正式业务卷的批准程序**。

供应链门槛仍未过：第二次使用 `--no-cache --pull=false --progress=plain` 的构建，在第 9 阶段下载 178.6 MB 的 `torch-2.6.0+cpu` wheel 后约十分钟没有新的阶段输出，由操作者中断；这是比上一次静默中断更窄的瓶颈定位，**不能记为无缓存重建成功或依赖错误**。主机到该精确 URL 的 HEAD 返回 200，1 MiB range GET 返回 206 且约 0.49 MB/s；Docker 构建容器的整轮下载/安装仍未完成，不重复对同一未收束路径无限等待。另用本机 `uv 0.9.18` 针对 Python 3.10、Linux/amd64 glibc 2.40、现有 139 项 constraints 试算出 139 个版本及 2,937 个 SHA-256 候选哈希；这只是解析结果，尚未落盘为项目 lock，也未以 `pip --require-hashes` 或干净构建器安装核验，不能提升为已锁供应链。正式 registry、服务器配置和可信 HTTPS 域名/证书仍未确定；用户已确认继续**本机**验收而非以测试入口代替外部验收。

为区分桥接网络与上游大文件瓶颈，进一步对同一固定 PyTorch URL 做了 1 MiB 容器 range 探针：bridge 约 4.51 秒、host 网络约 3.00 秒。随后只改变构建网络为 `--network host`，再次从同一干净第三候选执行 `--no-cache --pull=false --progress=plain`；仍停留在第 9 阶段的 178.6 MB wheel 下载，超过 15 分钟无新阶段输出后中断。**没有产出新的无缓存镜像，也没有证据说版本约束本身失败。**在未建立带哈希的受控 wheelhouse、可续传的可信分发缓存或更可靠构建网络前，不应反复盲试相同下载；现有缓存命中的 `sha256:44e993...` 仍只是本机候选。

补充实测：主机对 PyTorch 完整 URL 的 90 秒传输仅收到 25.66 MB（约 0.285 MB/s），说明上述 10–15 分钟静默不足以判定卡死。给予更长窗口后，第三次无缓存 host 网络构建的 PyTorch 步骤在 **1196.3 秒**完成，进入第 10 步，依次下载 PyArrow 50.1 MB、Faiss 18.8 MB、NumPy 16.8 MB、SciPy 37.7 MB 等包。用户询问耗时后立即终止该长时构建；未等到第 10 步安装完成，**没有成功的无缓存镜像或依赖核验**。不能继续把最新诊断写成“停在 PyTorch”；实际限制是这台本机到依赖仓库的多包低吞吐。后续需先准备可审计、可复用的依赖分发输入，再安排有进度上报的受控重建，避免再次长时间无反馈。

## 续验：不触发大文件下载的候选哈希锁（同日）

为把“仅版本约束”推进到可验证的分发输入，使用本机 `uv 0.9.18` 对当前 `requirements.txt` 加 139 项 constraints，在 CPython 3.10 / Linux x86_64 manylinux 2.40 目标、仅二进制包及 PyPI + PyTorch CPU 双索引下生成**候选** `backend/requirements-linux-amd64-py310.lock`（266,980 字节，SHA-256 `2cb6cd133f3e91dad550371e3c49a7c2e9f635c9eb58a6ed6fc27edd29eb9b56`）。它有 139 个精确版本、2,937 个候选 wheel/分发文件 SHA-256；程序比对与原 constraints 的规范化包名/版本 **139/139 一致**，包括 `torch==2.6.0+cpu`。文件没有本地路径、可编辑安装或直接 URL。求解器因 PyTorch 附加索引和 PyPI 的同名包交叉可见而使用 `unsafe-best-match`；锁定哈希并不能取代索引来源与发行身份审查。

先把部署静态回归改成要求 Dockerfile 使用哈希锁，原候选 **2 failed**；然后将 Dockerfile 的两次仅版本约束安装收敛为一次 `pip install --require-hashes --only-binary=:all:`，显式 PyPI 和 PyTorch CPU 索引，只安装上述 lock。静态测试现在 **3 passed**，七个部署/迁移专项合计 **50 passed, 4 skipped in 11.10s**；跳过项依赖选择性启用的真实服务条件。用既有第三候选镜像、`--network none --no-index --dry-run --require-hashes` 读取完整 lock 退出 0，只证明文件语法与**既有已安装版本**可匹配，**不会重新下载并核验 wheel 哈希**。新 Dockerfile/lock/测试仍是主仓未提交部署候选；没有据此构建新镜像，不沿用第三候选的运行回放结论为新候选背书。下一门槛是受控干净构建器实际执行 hash 模式安装、核对轮子来源和实装版本，再以**新镜像 digest**重放上述服务/业务验收；在大文件分发可控前不启动另一轮长时盲构建。

## 续验：本机明确受信的 TLS 入口（同日）

先检查自签测试证书仍有效，SAN 同时包含 `localhost` 和 `127.0.0.1`。复用已停止、保留的隔离 project `cpaq1restore20261017d` 与原第三候选镜像（**不是**尚未构建的哈希锁候选），按只读根覆盖启动三数据库及 HTTP/worker/Beat，临时 Nginx 仍只绑定 `127.0.0.1:18443`。`output/q1-proxy-20261017/probe_tls_trust.py` 改用正常证书验证做单变量对照：系统默认信任库**拒绝**该自签证书；显式将本机 `localhost.crt` 作为该一次性测试入口的信任锚后，HTTPS `/health` 返回 200，均未用 `verify=False`。这只证明**测试客户端明确配置的本机信任关系**，不是公众/目标服务器信任该证书。

原 owner-bound 探针增加可选 `Q1_CA_CERT` 参数；本次设置为该证书绝对路径，所有 CSRF、注册、登录、收藏与删除请求均启用证书验证。确认隔离 Redis 中此前注册限流键已经自然过期（未删除或重置）后，两套新学生会话重放为 `PASS`：无 CSRF 写入 400 与 MySQL 零行、有效写入 200 与 MySQL owner 行、另一账号不可见、删除后 MySQL 零行。MySQL、Neo4j、Redis、uploads、Milvus 五类原冷快照 marker 随后再次全部 `PASS`。本机代理已停删，project 六服务已停止，隔离卷保留；没有触碰原业务卷。现在本机 HTTPS 窄链不再依赖关闭证书验证，但正式域名、可信 CA/证书部署及外部重放仍是停止项。

尝试离线复用长时构建的 BuildKit pip cache：`docker buildx du` 一度报告该 cache mount 约 375.7 MB；用另一个**无网络、只读检查脚本**挂载相同目标路径时看到 0 个文件，随后 cache 记录为 4 KiB。不能从 BuildKit 统计推断 wheel 字节可复用，故**没有**宣称已核验 PyTorch 分发文件 SHA-256，也不基于该缓存启动新的安装。该临时检查 Dockerfile/脚本位于被忽略的 `output/q1-cache-inspect-20261017/`，不属于应用候选来源。

## 续验：哈希锁候选的干净源码身份（同日）

为避免新锁文件停留在混杂工作树，使用**独立 Git index**、以主仓技术文档基线 `3ed204bcce35a4f8b27d4b8440dcd7f36b21a343` 为父提交，仅暂存逐项列出的 **23 个部署候选文件**，`git diff --cached --check` 通过，生成**仅本机**提交 `7353dd7fd864a21700af7e961e5d6448b5864854`、tree `09fa0d25495496a2917bc98c0c5bdf4acbba5047`。相比第三候选的 22 件，唯一新增的是 `backend/requirements-linux-amd64-py310.lock`；其 Git blob 为 `08b2f8a83ed8d9d04c4be378a0af6df3cdc59456`。独立 detached worktree 为被忽略的 `output/q1-source-20261017-hash/`，`git status` 干净，主仓 staging 仍为空。候选树与仍未跟踪的 22 个 Agent `.py` 精确比对：**0 个混入**；非技术/IP/真实简历材料亦不在增量白名单。

从**该干净 worktree** 用权威 `mkg` Python 运行七个部署/迁移专项，结果为 **50 passed, 4 skipped in 11.67s**。Windows 主工作树 lock 的 SHA-256 为 `2cb6cd...`，干净 worktree 因 Git 换行规范化是 `cf5991a3477fce3fd136cd789bb445cb7c93208cb295a8fae10919da42c6315d`，两者 UTF-8 正文在归一化换行后逐字一致；未来产物应绑定 Git blob/tree 与干净来源字节，而非把两种文件 SHA 混称同一产物。此步只固定**源码输入并证明静态回归**，尚未从这个新提交构建任何镜像，更未完成 wheel 哈希安装、运行回放或推送到 registry；旧第三镜像的 HTTP 证据不得转嫁给它。

## 续验：全新恢复副本上的 L0 入队与跨账号追踪（同日）

为补足“收藏岗位”之外的正式任务入口证据，又从已核验的原冷快照恢复到此前不存在的 `cpaq1restore20261017e`。只在该 project 的 uploads、Milvus 卷执行前述停机、标签、快照校验后的**顶层**所有权准备；三数据库健康，显式迁移到 `0007_student_portrait_snapshot`，只启动第三候选旧镜像 `sha256:44e993...` 的 HTTP，**不启动 worker/Beat**。这使入队状态可观察且不会误报模型处理完成。只绑定本机 `127.0.0.1:18443` 的临时 HTTPS 代理使用显式受信的测试 `localhost.crt`，没有关闭证书验证。

被忽略的本机探针 `output/q1-proxy-20261017/probe_l0_pending_owner.py` 以两套真实注册/登录的学生 Cookie 会话及 CSRF 请求，上传一份虚构 60 字节文本到 `POST /api/profile/student/upload?async=1`，得到 **202**、`pending`、`delivery=queued`；本人 `GET /api/pipeline/trace/<task_id>` 为 **200** 且仅有 `file_received`，另一账号为 **404**。对同一个 `task_dfe00a9cd87a` 直接从隔离 MySQL/文件/Redis 回读：`pipeline_task` 为 `L0_parse_resume/pending/0`、owner 与 `params_json.student_id` 一致，`pipeline_resume_dispatch` 有行，`pipeline_task_trace` 为 `file_received/completed/0`，上传文件存在、大小 60、规范化路径位于该 owner 目录，`cpa.pipeline` 队列长度为 1。五类原冷快照 marker 随后再次全部 `PASS`。**通过的是持久化入队意图和追踪权限，不是 L0 解析或最终业务结果。**

探针最初把数据库中未规范化的 `/app/app/../uploads/<owner>/...` 原样路径与 `/app/uploads/<owner>` 比较，导致末尾本地断言失败；文件及 owner 行均已在前述独立直查中存在。改为 `Path.resolve()` 后对**原任务**的持久状态断言通过，没有修改服务代码。立即整段复跑时，隔离 Redis 的 `3 per hour` 注册限流在第二个新账号返回 **429**；未清理限流键、未继续撞接口，也**不把第二轮记为通过**。首次请求的两账号 HTTP 结果及其事后独立状态回读构成本轮窄验收证据。

临时代理已停删，`e` 的 HTTP/MySQL/Neo4j/Redis 全部停止；该 project 九个隔离卷保留供复核，原业务卷未触碰。新哈希锁候选 `7353dd7` 仍**没有镜像**，不能继承上述旧第三镜像证据。后续需在新镜像与真实 worker/模型/OCR 条件下验证 L0 完成、owner-bound 解析结果与失败重试；本轮故意不启动 worker，队列中保留一条本机测试消息及对应 pending 任务，重放时不得误认为业务积压。
