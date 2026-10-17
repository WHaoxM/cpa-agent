# Q1 隔离卷冷备份与异项目恢复实证（2026-10-17）

**结论：**五个必需卷在全新 Docker Compose project 中，经停写、冷备份、校验、恢复到另一全新 project、重新启动并读取原始标记，已走通一轮。此证据仅覆盖卷归档/恢复与 MySQL、Neo4j、Redis 数据可读；**没有**可归属当前提交的应用镜像、HTTP/worker/Beat、HTTPS、正式账号或 owner-bound 业务结果，不是部署批准。

## 隔离边界与产物

- 源 project：`cpaq1isolated20261017a`；目标 project：`cpaq1restore20261017b`。二者都是本轮新建，启动前检查目标卷名不存在；未使用或修改原有 `cpa-agent_*`/历史验收卷。两组服务验证后均已停止，卷及容器保留以便复核，未执行 `down -v`。
- 只启动两组的 MySQL、Neo4j、Redis；无宿主机发布端口。基础镜像使用本机可解析的 digest；这不是从 registry 重新拉取的发布身份。
- 本机插值文件为被忽略的 `output/q1-isolated-20261017.env`，只含一次性测试凭据，不提交。归档在 Ubuntu 22.04 WSL 的 `/var/tmp/cpa-q1-snapshot-20261017b/`，`snapshot.json` SHA-256 为 `f78ccc76d605f7a9a0bf3d9c54c4f1a1199cf62518f08d39dd52b816f40741d4`；五个压缩归档合计 6,810,022 字节。不得把它当作正式数据备份或分发。
- 归档 helper 是仅包含当前 `volume_archive.py` 的本机最小镜像，不包含应用或 22 个未审 Agent 文件。底层为本机 `python@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d`，构建上下文仅 `backend/scripts/ops/`，`COPY` 仅该脚本；脚本在镜像内与本机的 SHA-256 同为 `a583f48206fe68b48480d08181d99d2dfc662bfab835defa6b2577e4b30de591`，本机镜像 digest 为 `cpa-q1-volume-helper@sha256:039b2c4893a85984d817fd40ca6192bc6010d989aaac45985aa1b54a5717c040`。它只用于卷演练，不是应用发布镜像。

## 先失败、后修复

1. 新源卷启动后，MySQL 健康且 `cpa_agent` 初始有 50 张表，Neo4j `RETURN 1` 与 Redis `PING` 均成功。写入无敏感标记：MySQL 表 `q1_restore_probe`、Neo4j 节点 `Q1RestoreProbe`、Redis 键 `q1:restore:marker`，另在 uploads/Milvus 两卷写 `q1-marker.txt`。
2. 停止全部源服务后，原始 `volume_snapshot.py backup` 在首个 MySQL 卷失败。实际 `/var/lib/mysql/mysql.sock` 是指向 `/var/run/mysqld/mysqld.sock` 的悬空运行时符号链接；归档工具一律拒绝链接，故无法备份官方 MySQL 容器刚停止的数据。Windows Docker Desktop 写入的根用户 `0600` 归档文件同时无法由 Windows 宿主进程读取；首轮不完整目录 `output/q1-snapshot-20261017a/` 保留为失败证据，不能作为快照使用。
3. 新增两条先红的回归，分别证明编排未显式传递 MySQL 排除策略、归档器没有该策略。修正只在 `mysql_data` 的 backup 命令显式允许**根路径、精确目标**的上述运行时 socket 链接；其他链接和所有归档内链接继续拒绝。参数对 restore 无效。额外负向测试覆盖同名错误目标及异名同目标。`mkg` Python 执行 `python -B -m pytest -q backend/tests/test_volume_snapshot.py backend/tests/test_volume_archive.py` 为 **18 passed**。代码仍属于本机未跟踪的部署候选，未作为可发布代码提交。
4. 因 Windows 宿主对容器创建的 `0600` 文件不可读，成功回放改在同机 Ubuntu 22.04 WSL 以 root 运行标准库编排脚本，归档放 WSL Linux 文件系统；不降低备份文件权限。脚本 `backup`、`verify`、异项目 `restore` 均返回 0。恢复后两组真实数据库容器健康；目标 MySQL 返回 `mysql-q1-20261017a`，Neo4j 返回 `neo4j-q1-20261017a`，Redis 返回 `redis-q1-20261017a`，uploads/Milvus 两卷各自返回原标记。目标卷 Compose owner 标签与目标 project 一致，源 project 没有运行容器。

## 最短复核路径与下一门槛

从仓库根目录，先核对这两个 project 的卷与已停止服务；**不要**对现有目标重新执行 restore（它应拒绝已存在卷）。为一次新的演练，换用全新源/目标 project 名，准备独立插值文件与五个新源卷，明确只启动 `mysql neo4j redis`；写标记、停写后，在 WSL Linux 路径运行：

```bash
python3 -B backend/scripts/ops/volume_snapshot.py backup --project <new-source> --directory <new-linux-backup-path> --env-file <local-fixture-env>
python3 -B backend/scripts/ops/volume_snapshot.py verify --directory <new-linux-backup-path>
python3 -B backend/scripts/ops/volume_snapshot.py restore --project <new-target> --directory <new-linux-backup-path> --env-file <local-fixture-env>
```

启动目标 DB 容器后必须按服务原生客户端回读业务标记；归档哈希与容器健康本身不够。当前 helper 镜像仅测试归档算法，尚需在**干净来源的同一应用镜像**中复验脚本、固定依赖与入口，再补 HTTP/worker/Beat、正式身份、HTTPS 和 owner-bound 业务结果。服务器、registry 与受信任域名未确定，均是 Q1 后续停止项。
