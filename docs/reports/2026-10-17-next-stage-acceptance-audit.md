# 下一阶段验收对账（2026-10-17）

**状态：Q3、Q4 的文件取舍任务已形成独立结论；Q1 仅完成本机第三候选的隔离窄链验收，整体 goal 仍未完成。**本页按用户要求逐项对齐证据与缺口，不把报告、静态测试或旧镜像结果互相替代。开始下一轮时仍须以实际工作树、镜像、卷和外部入口重新核对。

## Q1：隔离部署、恢复与业务结果

| 验收项 | 当前证据和判定 | 尚缺什么 |
|---|---|
| 不碰原业务卷、不混入 22 个未审 Agent 文件 | 本轮操作限定 `cpaq1isolated20261017a` 及 `cpaq1restore20261017{b,c,d,e}`；五个 project 分别保留 6/9/9/9/9 个卷，相关运行容器均为 0。旧第三镜像来源 `c45819c3b04834cb9404c7ff628be1ab8e216324` 无 22 个实验文件；新哈希锁来源亦已从精确 **23 文件白名单**生成本机干净提交 `7353dd7fd864a21700af7e961e5d6448b5864854`、tree `09fa0d25495496a2917bc98c0c5bdf4acbba5047`，与当前 22 个未跟踪 Agent 路径交集为 0。**来源隔离边界已核验。** | 新源码身份不能沿用旧镜像的运行证据；仍须从该提交构建并验证新镜像。 |
| 冷快照与全新卷 | [恢复报告](2026-10-17-q1-isolated-volume-recovery.md)记录停写、备份/verify、异 project 恢复；`b`、`c`、`d`、`e` 四个此前不存在的目标 project 均从原冷快照起步。最新 `e` 的五类 MySQL/Redis/Neo4j/uploads/Milvus marker 为 `PASS`，显式 Alembic 迁移后 `current=head 0007_student_portrait_snapshot`。**隔离恢复窄范围通过。** | 正式业务备份目标、保留周期和原业务卷维护窗口未定；不能在原卷直接试恢复。 |
| 同镜像 HTTP/worker/Beat 与运行边界 | 固定旧镜像 `cpa-q1-app@sha256:44e9931020ad8ee263de346e0d5e09fe586073d61a6fd8352299708a6bc7c658` 在恢复 project 上执行 Gunicorn、worker、Beat 和显式迁移；三数据库健康、HTTP `/health` 200、worker inspect 在线，Beat 实际投递并被 worker 消费。`d` 的 HTTP/worker/Beat 根只读、`cap_drop=ALL`、`no-new-privileges`，六处预期写目录可写、根目录拒写。**现有第三候选窄运行链通过。** | Beat 的重放队列为空，不是完整简历/图谱任务成功；资源压测、信号停止与真实 OCR/模型/Milvus 写入未通过。 |
| 已复现的窄缺陷 | MySQL 停机 socket 链接导致首次归档失败，已限定该特例并做负向回归；镜像默认 `run.py` 曾不安全，改为 fail-closed Gunicorn；恢复 uploads/Milvus 卷顶层 `0:0/0755` 曾使 UID 10001 写入报 `PermissionError 13`，仅在隔离卷停机且确认备份/标签后调整**顶层目录**并复验。详见[恢复报告](2026-10-17-q1-isolated-volume-recovery.md)和[运行回放](2026-10-17-q1-candidate-runtime-replay.md)。**对应测试状态下已修/规避。** | 真实旧卷内部 root-owned 子目录、数据库/WAL 不能由顶层调整推断可写；须按实际数据另做所有权清单与维护批准。 |
| 受信入口和 owner-bound 业务结果 | 本机仅绑定 `127.0.0.1:18443` 的 HTTPS 代理：默认客户端拒绝自签证书，明确指定一次性 `localhost.crt` 后正常验证通过；正式学生 Cookie 会话在**证书验证开启**时完成 CSRF、注册/登录、无令牌拒写、收藏写入、跨账号不可见、删除和独立 MySQL owner/零行回读。**本机显式信任 + 收藏窄链通过。** | 自签测试证书不是目标服务器/公众信任；真实域名/可信证书、正式入口和 owner-bound 简历任务到最终业务成果仍缺。 |
| 正式 L0 任务入队与追踪 | 在全新恢复 `e` 上仅启 HTTP、不启 worker/Beat；显式受信本机 HTTPS 的两套正式学生会话完成虚构简历上传 **202/pending/queued**、owner trace **200**、他人 **404**；独立回读 MySQL 任务/dispatch/trace、owner 上传文件及 Redis 队列均成立，五类冷快照 marker 未变。探针首轮仅因测试代码比较未规范化路径在末尾失败，改为规范化后对**原任务**的状态复核通过；整段复跑被注册限流 **429** 中断，没有重置限流或计作成功。详见[镜像运行回放](2026-10-17-q1-candidate-runtime-replay.md)。**仅入队意图与权限通过。** | worker、模型/OCR 未启动，任务仍 pending；L0 解析完成、owner-bound 最终结果及失败重试均未通过。旧镜像证据不能转嫁给新哈希锁候选。 |
| 镜像/依赖可复现与分发 | 第三镜像固定本机基础 digest，139 项安装版本与约束一致；新 `requirements-linux-amd64-py310.lock` 有 139 个版本/2,937 个候选哈希，静态比对和既有镜像无网络 dry-run 通过。新白名单干净来源 `7353dd7` 在独立 worktree 运行部署/迁移专项为 **50 passed, 4 skipped**。长时无缓存构建完成过旧 Dockerfile 的 PyTorch 步骤，但在后续大包下载时按用户反馈终止；**新 hash-mode Dockerfile 未构建、未运行。**对 BuildKit cache 的离线检查未拿到可复用 wheel 字节。 | 必须从新干净候选实际执行 hash-mode 安装、`pip check`、包清单与镜像 digest 比对，并重新回放服务/业务；还需可信 registry 的按 digest 拉取、来源与签名/证明。当前不能发布代码候选或镜像。 |

第三候选的[完整命令、镜像身份与逐项证据](2026-10-17-q1-candidate-runtime-replay.md)是本机重放入口；它不是新哈希锁候选的验收。服务器配置、镜像仓库及可信 HTTPS 域名/证书尚未确定，用户已明确要求继续本机验收；不以自签或本机 `docker compose` 代替正式验收。

## Q3、Q4 与工作区保护

| 交付单元 | 判定 | 保留的后续队列 |
|---|---|---|
| Q3 Agent 候选 | [逐项取舍](2026-10-17-q3-agent-candidate-disposition.md)覆盖当前仍可见的 **22/22** 个未跟踪 `.py`；各自对照现行实现和本机 OpenCode/Codex 上游快照，默认 Agent HTTP 多轮只用确定性替身完成窄回放。当前 **0 接线、0 删除**，均继续隔离，其中不安全原样接线者已有标记。**逐项分流任务完成，不等于 Agent 真模型全验收。** | 只有复现具体默认请求缺陷后，才单项决定复用/薄适配；不得以草案清单整包推进。 |
| Q4 独立前端仓 | [13 件分流](2026-10-17-q4-frontend-worktree-review.md)与当前独立仓 `git ls-files --others --exclude-standard` 数量一致；来源/页面关系/归属限制分别记录。Mock 岗位介绍串文案缺陷在独立分支 `codex/q4-role-intro-mock-boundary` 提交 `da7a3fa` 并推送，浏览器回放和构建通过，主仓未纳入这些文件。**独立处理结论形成。** | 13 件仍待相应归属人决定是否发布；`check:ability-intro` 三个缺失岗位仍红，不能猜文案；Mock 不代表后端 HTTP。 |
| 保护和版本 | 主仓、前端仓的技术提交分别在自己的分支且本地/远端一致；主仓 staging 空。非技术竞赛/IP 文件与真实简历审计材料原位保留，主仓 `.git/info/exclude` 仅在本机防误加，不当作文件删除；来源不明的小文件未清理或提交。 | 新克隆不继承本机 exclude；维护者提交前继续按精确路径核查。任何 Q1 部署源码须等**新产物对应门槛**，不与文档或 Q3 实验混交。 |

当前导航与领取顺序见[工作入口](../current-work.md)。下一项**可在本机独立推进**的是：先稳定可信的 wheel 获取/缓存和构建进度报告，再从只含部署白名单的新来源验证哈希锁镜像；真实旧卷内部 owner 清单和完整任务结果另列专项。**外部停止项**是服务器规格与权限、镜像仓库、可信域名/证书、正式密钥/模型/OCR 等真实依赖。在这些条件未出现前，不宣称 Q1 或整体 goal 完成。
