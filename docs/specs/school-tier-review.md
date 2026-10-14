# 学校评分档位的官方证据人工审核（首期）

## 边界与状态

学校名称和简历原文仍属于学生确认的 L0；管理员**不能**改写 L0。`school_tier_review`
是独立、可撤回的 L2 评分输入。当前首期只批准 `985`、`211`、`双一流`，且须从可核验的
官方页面同一连续摘录中看到准确学校原名与档位。普通本科、没有招生年份和招生省份的
一本/二本/三本、仅模型推断、学生自报、一般网页与未经登记的学校站点均不可映射，
保留 L2 `partial`。这不是通用学校分类知识库，也不把教育部或学校官网上的任何句子
自动解释为当前学生的具体入学批次。

首期来源白名单：教育部精确 HTTPS 主机 `moe.gov.cn`、`www.moe.gov.cn`；学校官网精确
学校-主机登记 `清华大学→www.tsinghua.edu.cn`。不接受通配 `.edu.cn`/`.gov.cn`、HTTP、
跳转或只凭域名后缀判定真实性。教育部一些历史页面当前 HTTPS 302 至 HTTP，故该页面
**不可**通过审核；清华大学[学校沿革原文](https://www.tsinghua.edu.cn/xxgk/xxyg.htm)
已用当前抓取器实际取得 HTTPS 200，正文同段包含学校原名及三种建设工程名称。
审核者仍须核对全文语境；URI/摘录必须逐字出现在当前抓取到的可见正文。抓取器做
DNS 公网地址校验、固定连接 IP 和 TLS SNI/证书主机、禁止重定向、限制响应大小和
连接/读取超时；源不可核验时 fail closed，不提交审核行。

## 管理员操作合同

全部入口要求平台**自建正式账号**登录且为 active admin，不能以调试 `g.cpa_admin`
代替。写操作同一 MySQL 事务校验管理员与当前已确认学生资料、锁定 owner 的当前
`student_profile` 与对应审核版本，写审核状态及 `admin_audit_log`。审核记录包括
原学校名、L0 确认任务 ID、精确档位、来源类别与 URI、服务端抓取响应 SHA-256、
可见正文摘录及其 SHA-256、整体证据摘要、管理员 ID、审核时间；审核内容相同则
幂等回读，不重复记账。新批准取代前一版本；撤回须提交前端回读的版本摘要与理由。

- `POST /api/admin/school-tier-reviews`：JSON 精确字段 `student_id`、
  `confirmation_task_id`、`school_name`、`school_tier`、`source_kind`、`source_uri`、
  `source_quote`、`confirm_official_evidence:true`。`source_kind` 为
  `education_authority` 或已登记学校对应的 `school_official`。新版本 201，原版本
  幂等 200，证据或 L0 变化 409，官方源/数据库不可用 503。
- `GET /api/admin/school-tier-reviews/current?student_id=...&confirmation_task_id=...`：
  回读完整审核证据和状态。未迁结构为 503，不伪装成“无结果”。
- `POST /api/admin/school-tier-reviews/<id>/revoke`：提交
  `expected_evidence_sha256`、`reason`；成功 200，版本变化 409。

生成 L2 时只把有效审核的 `id` 与 `evidence_sha256` 作为
`reviewed_official_school_tier` 来源写入画像；保留学生原确认事实，即使其中类别
写错也不被偷偷改成官方档位。生成写库前再次锁定并比对 L0/审核版本，避免并发撤回
后仍落旧分。公开 4D 读取、Agent 资源引用及 L3/L4 匹配/报告前必须比对当前审核版本；
撤回或换版后的旧画像不得继续评分、匹配或展示。仅当 schema **尚未迁到 0005** 且
画像没有审核引用时，旧 L2 读写路径按原证据规则继续运行；已经迁到 0005 却缺审核表
视为数据故障，不把它当作“审核为空”。

## 隔离验收与部署限制

当前 live MySQL 的 Alembic 账本尚未接管；本切片**只在唯一命名的一次性 MySQL 库**
执行 `0004→0005`，没有对 live 业务库做 DDL、stamp 或停服。`0005` 代码可由旧库
兼容门禁启动，但管理员审核功能在维护窗口迁移前不可用。正式启用必须按
`docs/runbooks/mysql-migrations.md` 备份、停写、恢复演练并升级；仅版本号等于 head
不等于用户业务验收。

从仓库根目录、`mkg` 环境运行隔离测试，系统临时目录放 D 盘：

```powershell
$env:PYTHONUTF8='1'
$env:TEMP='D:\AGeneral Workspace\cpa-agent\output'
$env:TMP=$env:TEMP
$env:TMPDIR=$env:TEMP
$env:CPA_TEST_LIVE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q `
  backend/tests/test_school_tier_review_http_mysql.py `
  backend/tests/test_mysql_migrations.py `
  --basetemp='D:\AGeneral Workspace\cpa-agent\output\pytest-school-review'
```

该回归使用真实自建账号、真实本地 HTTP、真实隔离 MySQL 和 Redis 会话；只把学校
官网响应模拟为固定可见原文，以确保测试不依赖外站在线状态。测试先证明 0004
未迁状态的无审核 L2 完成及 4D 可读、换 L0 后旧画像 409、错置档位 L2 partial/
管理员 503，再迁 0005，验证非管理员拒绝、错误摘录拒绝、
批准→L2 完成与画像回读→撤回→画像 409、L3/L4 partial、无匹配/报告落库，
审计 2 条。另用真实 HTTPS 抓取器对清华大学原文做只读抽取核对。

Agent 基础能力复用核对：本机 OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`
的 `packages/opencode/src/session/prompt.ts` 及 Codex
`a06545b311fe01e51ce855c7aa5d8da21e9e7aaf` 的
`codex-rs/protocol/src/request_user_input.rs` 用于对照“结构化输入与确认分离”；
两者均不提供学校评分证据规则。这里复用项目既有的 L0 确认事务、
`PipelineRunner` 和管理员账号/审计模式，没有创建另一套通用 Agent 状态机。
