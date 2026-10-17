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
