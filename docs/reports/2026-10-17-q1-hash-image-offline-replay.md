# Q1 哈希锁镜像：本机离线复现与恢复回放（2026-10-17）

**判定：本机隔离门槛取得明确进展，但不是正式发布批准。**从固定基础镜像、干净源码和整份哈希锁取得全部分发输入，两次断网/无缓存构建在时间戳规范化后生成**逐字节相同**的 OCI 归档；最终归档加载的新镜像又在全新冷恢复 project 上完成数据库、HTTP/worker/Beat、HTTPS 正式账号 owner-bound 收藏写删的窄链。正式 registry/服务器/可信域名及完整简历解析成果仍缺。不要把本报告与先前第三候选镜像混作一个产物；历史回放见[第三候选及前序门槛](2026-10-17-q1-candidate-runtime-replay.md)。

## 输入身份与分发文件

- 应用源码：仅本机干净提交 `7353dd7fd864a21700af7e961e5d6448b5864854`，tree `09fa0d25495496a2917bc98c0c5bdf4acbba5047`，独立 worktree `output/q1-source-20261017-hash/`；该树来自 **23 个部署候选文件白名单**，未审 22 个 Agent `.py` 交集为 0。该提交**尚未推送**。基础镜像固定 `python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d`，实际为 Linux/amd64、Python 3.10.22、glibc 2.41。
- 锁文件：干净 worktree 的 `backend/requirements-linux-amd64-py310.lock`（该 checkout 字节 SHA-256 `cf5991a3477fce3fd136cd789bb445cb7c93208cb295a8fae10919da42c6315d`）；139 个精确版本、2,937 条分发哈希。被忽略的本机 `output/q1-wheelhouse-20261017/` 有 **149 个目标平台 wheel、411,117,291 字节**，覆盖 139 个包及少数兼容备选。`uv 0.9.18` 的目标 Python 3.10 / Linux x86_64 glibc ≥2.40 解析结果与锁的包名/版本 **139/139 相同**，每个选定 wheel 的 SHA-256 均在对应锁条目内；`fetch_lock_wheels.py` 先核对 HTTPS 来源、精确字节范围/总长，再只在整文件哈希命中后暴露 `.whl`。中断分段留在忽略目录供续取，不进入构建 context。
- 一个来源差异被显式处理：`uv` 给 Jinja2 返回的 PyTorch 镜像链接没有哈希；脚本未采信该链接，而从 PyPI 版本元数据选同名 wheel，要求其 SHA-256 **已在项目锁内**。另一个 CDN 对不同 Range 响应返回不同 ETag；因此不以 ETag 等值代替内容证明，仍逐段要求 HTTP 206 和准确 `Content-Range`，最后以整文件 SHA-256 决定是否接纳。
- 固定基础镜像内、**无网络**执行完整 `pip install --dry-run --ignore-installed --no-index --require-hashes --only-binary=:all:`，仅从上述 wheelhouse 解析出 139 个待装包并退出 0。此前小 wheel 错哈希负控非零退出；完整 wheelhouse 的真实安装在下面的断网构建中通过。wheelhouse、下载脚本与离线 Dockerfile 都在被忽略的 `output/`，**不随 Git 文档分发**，外部重放必须先准备等哈希分发输入。

## 无缓存构建与字节级对照

本机离线 `output/q1-wheelhouse-20261017/Dockerfile.offline` 以同一干净 `backend/` 为 context；相对仓内 Dockerfile，构建用只读命名 context `/wheels` 取代两个实时索引，并增加 `pip --no-compile`。离线文件 SHA-256 为 `5dd0713a6c67de0b4df17a0a3ad4ee0210de1a454926bc645f9fe9c9f3f1189d`。两次构建均使用 `--platform linux/amd64 --no-cache --pull=false --network none`、`SOURCE_DATE_EPOCH=1792233104`（上述源码提交时间）及 OCI exporter 的 `rewrite-timestamp=true`。固定基础层允许本机已存在，但**应用 RUN/COPY 层无缓存、安装时无网络**；未证明镜像仓库重新拉取的来源或签名。

第一次在**未禁用** `.pyc` 的变体上，两次无缓存镜像 ID 不同；即使固定时间戳并重写 OCI 层，唯一不同的是 `pip install` 层，逐文件内容哈希确认 **17,199 个差异全是 `.pyc`，非 `.pyc` 文件内容差异为 0**。据此给当前未提交的主仓 Dockerfile 加 `--no-compile`，先让部署回归 **1 failed**、再修至七组部署/迁移专项 **50 passed, 4 skipped in 9.25s**。这是一项已复现的窄域修正；静态断言本身不能替代下面的镜像对照。关于 `SOURCE_DATE_EPOCH` 和 exporter 重写选项，采用 [Docker 官方可复现构建说明](https://docs.docker.com/build/ci/github-actions/reproducible-builds/)与[镜像 exporter 参数](https://docs.docker.com/build/exporters/image-registry/)规定的边界。

禁用预编译后，同样输入**独立执行两次**断网、无缓存完整构建：`q1-hash-nopyc-one.oci.tar` 与 `q1-hash-nopyc-two.oci.tar` 各 **439,511,552 字节**，两者归档 SHA-256 均为 `32f6b28aa816001349af37d7f587b104cf3bb0886ee881d82c6f7e953e0dcd22`；OCI manifest 均为 `sha256:72ca6374ac644f6fe37fec4237ee9211182153bf16161845332ba67148a533b8`。加载首份归档得到同 ID 本机镜像 `cpa-q1-hash-repro:20261017`，`USER=10001:10001`，默认命令为 fail-closed Gunicorn；`pip check` 为 `No broken requirements found`，镜像内 139 项实装版本与锁 **0 差异**，抽查未审 Agent 文件不存在。**这是同一台本机、同一 wheelhouse 与构建器的字节级复现，不是跨机器/registry 复现。**

最小构建命令（PowerShell，仓库根目录，需先核验本机 wheelhouse 的 149 个文件；`output/` 均为本机忽略产物）：

```powershell
$src = (Resolve-Path 'output/q1-source-20261017-hash/backend').Path
$wheelhouse = (Resolve-Path 'output/q1-wheelhouse-20261017').Path
$dockerfile = (Resolve-Path 'output/q1-wheelhouse-20261017/Dockerfile.offline').Path
$dest = Join-Path $wheelhouse 'q1-hash-nopyc-one.oci.tar'
docker buildx build --platform linux/amd64 --no-cache --pull=false --network none `
  --build-arg SOURCE_DATE_EPOCH=1792233104 --build-context "wheels=$wheelhouse" `
  --output "type=oci,dest=$dest,rewrite-timestamp=true" `
  --progress plain -f $dockerfile $src
```

## 最终镜像的全新冷恢复运行回放

从原 WSL 冷快照 `/var/tmp/cpa-q1-snapshot-20261017b/` 再次 `verify`（`snapshot.json` SHA-256 `f78ccc76d605f7a9a0bf3d9c54c4f1a1199cf62518f08d39dd52b816f40741d4`）并恢复到此前不存在的 `cpaq1restore20261017g`。恢复器只创建该 project 的五个目标卷；在进程启动前，核对两文件卷标签、无运行占用及顶层 `0:0` 后，只对 uploads/Milvus **顶层**改为 `10001:10001`，没有递归改文件或碰原业务卷。三数据库健康；Alembic `current` 迁移前为空，显式迁移后为 `0007_student_portrait_snapshot (head)`。

同一最终镜像 `sha256:72ca6374ac644f6fe37fec4237ee9211182153bf16161845332ba67148a533b8` 用于 HTTP、worker、Beat 和迁移：三个应用进程 `ReadonlyRootfs=true`、`CapDrop=[ALL]`、`no-new-privileges=true`，HTTP `/health` 为 200、Celery inspect 为 1 个在线节点。六处预期数据目录能写入/回读/删本机探针，`/app` 与 `/home/cpa` 拒写；MySQL/Redis/Neo4j/uploads/Milvus 五类冷备份标记在业务请求前后均 `PASS`。Beat 实际发送 5 个计划任务，worker **5 received / 5 succeeded / 0 ERROR**；其中重放队列为空，**不是完整模型/OCR/简历任务成果**。

本机 Nginx 仍只绑定 `127.0.0.1:18443`，自签 `localhost.crt` 的系统默认信任检查失败，客户端**显式指定该测试证书**后 HTTPS `/health` 200。两套正式学生 Cookie 会话经 CSRF、注册和登录后，无令牌收藏写入 **400 且 MySQL 零行**；有效写入 **200**，独立 MySQL 回读只有登录 owner 行、请求体伪造的另一账号 ID 与 `role=admin` 未获采信；owner 列表可见、另一账号不可见，删除后再次 MySQL 零行。被忽略的探针是 `output/q1-proxy-20261017/probe_https_owner.py`，本轮输出 `PASS`。随后临时代理停删，`g` 六服务全停，九个隔离卷保留；`f` 的较早同类试验也已全停且九卷保留。

## 未过门槛与接手动作

1. 最终可复现镜像使用的是 **`7353dd7` 应用源码 + 单独的本机离线 Dockerfile**；当前主仓 Dockerfile/test 的 `--no-compile` 修正仍未提交，尚未从其**新干净 Git 身份**构建/比对，不得把本机离线产物叫作仓内 Dockerfile 的发布物。下一步把这两处最小修正单独冻结为新候选来源，保留精确离线变体/锁/wheelhouse 身份，再从该来源复验；不要混入 22 个 Agent 草案或其他未归属文件。
2. wheelhouse 没有独立可信 registry/签名/供应链证明；目标服务器、镜像仓库、正式密钥管理及可信 HTTPS 域名/证书仍未确定。自签证书**只在本机显式信任**，不能替代外部入口验收。
3. 本轮正式业务结果是 owner-bound 收藏岗位写删；L0 简历只在旧镜像的另一隔离 project 验到持久 `pending` 入队。真实模型、OCR、Milvus 数据写入、L0 最终解析与后续 L2/L3/L4 成果、完整故障恢复及真实旧卷内部权限仍未通过。不得从 Beat 空队列成功或 `/health` 推断这些项。

本机脚本和 OCI/轮子产物均在 `output/` 下被忽略；不删除、提交或推送归属不明材料。主仓技术文档可单独提交，部署源码仍按上述新来源门槛继续隔离。
