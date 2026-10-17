# 生产部署工作树分流核查（2026-10-17）

**结论：**当前是有局部实现和静态测试证据的**生产化候选工作流**，不是已冻结、可发布的镜像或可升级现有数据卷的部署方案。本报告只审查本机工作树；未构建、推送、迁移、启动生产 Compose 或触碰业务卷。

## 来源与交付单元

- 盘点时主仓位于 `main`，HEAD 为 `a8ef1f15e622413dd3bb819d858849e944d22620`。部署相关的 Dockerfile、Compose、环境模板、测试、`DEPLOY.md` 和迁移手册仍有未提交改动；`backend/production_wsgi.py`、`backend/scripts/ops/`、四个专项测试、`docker-compose.prod.host-proxy.yml`、本机 `docs/specs/production-image-hardening.md` 仍是未跟踪文件。固定 HEAD **不包含**这些实现。
- `backend/Dockerfile` 仍 `COPY . .` 且默认 `CMD ["python", "run.py"]`；Compose 覆盖为 Gunicorn 才使用 `production_wsgi:create_production_app()`。从此脏工作树本地构建还会打包[22 个未跟踪 Agent 候选](2026-10-17-untracked-agent-modules-triage.md)，不能把镜像指认为 HEAD 的发布物。
- 主仓未发现已跟踪的 `.github/workflows/` 发布/构建工作流；外部 CI 或人工构建记录未核验，不能据此断言项目不存在其他流水线。

## 分层状态

| 层次 | 当前证据 | 不能推出什么 |
|---|---|---|
| 配置结构候选 | 生产 Compose 要求显式 project 名、`@sha256:` 镜像引用、资源限制；默认不发布宿主机端口；HTTP/worker/Beat/显式迁移使用同一应用镜像。宿主机代理覆盖层仅绑定 loopback。 | 占位 digest 不是真实已发布镜像；Compose 可解析不证明容器可启动或 HTTPS 可用。 |
| 应用入口候选 | Compose HTTP 命令是 Gunicorn；`production_wsgi.py` 在建 Flask app 前拒绝 demo/debug、弱密钥、不安全远端 Origin和落后 Alembic head，且只读版本账本不自动迁移。 | Dockerfile 的默认命令仍是开发服务器；单测模拟的配置/数据库不证明真实生产进程和数据库已通过。 |
| 冷快照/恢复候选 | `volume_snapshot.py`/`volume_archive.py` 针对五个必需命名卷，设计为停写、源卷只读、恢复到新 project，并有摘要/危险路径拒绝测试。 | 临时目录往返与模拟 Docker 调用不证明真实命名卷可恢复，更不证明恢复后的业务数据可读。 |
| 文档与计划 | [`DEPLOY.md`](../../DEPLOY.md) 已明确“暂不可正式上线”，本机未提交的镜像提案列出 P0–P5 停止条件。 | 文档写出将来步骤不表示镜像仓库、服务器、HTTPS、锁文件或运维批准已经存在。 |

## 本轮实际验证

在仓库根目录使用 `mkg` Python 3.10 执行：

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_compose_deployment.py backend/tests/test_release_preflight.py backend/tests/test_production_wsgi.py backend/tests/test_volume_archive.py backend/tests/test_volume_snapshot.py
```

结果：**39 passed（8.79s）**。其中 Compose 用占位镜像和测试环境变量解析配置；WSGI/快照测试采用假配置、临时目录或模拟 Docker。未运行 opt-in 真实 MySQL/Redis/worker/Beat 测试，也未做真实镜像、容器、HTTPS、命名卷恢复或学生业务旅程。`git diff --check` 对本轮已跟踪文档/忽略规则无空白错误；这不是部署验收。

### 续验：静态预检的环境一致性缺口

同日复核时 Docker Engine 28.5.2 可访问，但没有运行中的容器；已有本地应用镜像未证明与本轮脏工作树对应，故没有拿旧镜像充当当前发布产物。预检脚本 `backend/scripts/ops/release_preflight.py` 原来只要求 HTTP、worker、Beat 镜像一致，却只检查 HTTP 环境：在其他字段不变时将 worker 的 `CPA_DEMO_MODE` 单独改为 `true`，`check_configuration` 仍返回空错误列表。新增两例回归在修正前均失败；本机候选脚本现要求三进程的解析后环境一致，针对当前 Compose 的实际解析模型，基线返回 `[]`，单改 worker demo 后返回 `HTTP, worker, and Beat must use the same backend environment`。五份专项测试现为 **41 passed（6.35s）**；用只含占位凭据与镜像摘要的环境运行预检 CLI，返回明确的 **static admission PASS**，仍不代表真实镜像、数据库或业务可用。

上述脚本和测试仍属于**未跟踪的部署候选**，没有随本报告进入已发布代码；提交该候选前必须成对包含修正与回归，并继续履行下述产物/恢复/使用者路径门槛。

## 不混合的下一步

1. **先定来源**：把部署所需源码、配置和测试从其他未跟踪实验中划清；不得 `git add -A`、从脏目录构建并给它贴正式版本。建立干净固定提交后才有可审查构建输入。
2. **完成可重复产物**：固定基础镜像和 Python/CPU wheel 依赖，改变镜像默认开发入口，生成并从仓库按 digest 重拉同一镜像；锁文件、SBOM/provenance 与产物摘要需绑定来源提交。
3. **再验真实恢复**：用隔离 Docker project 和新卷做一次冷快照/恢复，核对 UID/GID、MySQL/Neo4j/Redis、上传文件及 owner-bound 业务读取；未完成前不升级已有卷。
4. **最后验使用者路径**：服务器资源、受信任 HTTPS、密钥边界和迁移条件明确后，经正式账号执行真实简历任务并回读权威成果；`/health`、39 个静态测试和卷哈希不能替代它。

**当前发布停止条件：**来源/产物身份未冻结、依赖/基础层未完全锁定、无可核验 registry digest、真实卷恢复和 HTTPS/业务旅程未验收。继续本地改造可以；正式发布或现有生产卷升级不能据本轮证据批准。
