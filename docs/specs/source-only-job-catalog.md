# 来源已核验、暂不可评分岗位的公开目录

> 现行旧库加表入口为 [MySQL 统一迁移](../runbooks/mysql-migrations.md) 的 `alembic upgrade head`；下文单独执行 SQL 的段落是历史部署记录。

状态：2026-10-13 用户确认：官方岗位来源已核对，但资格条件超出当前评分器时，**可以先公开展示，必须禁止评分与匹配**。2026-10-14 又确认公共读取改用最长 5 分钟的后台核验缓存；故障时隐藏来源专用条目、保留平台已发布岗位。这不是放松 `job_catalog_publication` 的评分门禁。

## 独立信任层

- `job_catalog_review` 是模型审查，可能 `blocked`；不因 `candidate_ready` 自动成为人工发布。管理员账号需确认精确模型审查摘要与固定官方记录摘要，服务端重新读取固定 CSB 官方 feed、验证同一 `jobid` 的原始记录未变且截止日期未过，再把**原始来源**作为 `source_only` 独立版本写入数据库。客户端不能提供 URL/岗位正文来覆盖来源，不抓取任意 URL。
- 独立 `job_catalog_source_listing` 保留原始记录、feed/记录摘要、核验时间、原模型审查 ID/摘要、管理员身份/时间、版本及 `listed|revoked` 状态；审计与版本同事务提交。每个来源记录只展示最新版本，最新版本撤回后不回退旧版。无证据的模型结论/资格摘要不得混进公开原文。
- 公共目录和详情可展示标题、官方原文、来源 URI、核验时间及来源宣称的截止时间，但须 `match_eligible=false`、`scoring_status=not_supported`、`vacancy_validity=unknown`。公共请求**不访问官方上游**，只使用 Redis 中最长 5 分钟的后台核验摘要；重复 ID、删除或内容变化在下次成功核验后隐藏旧快照，最晚受 5 分钟窗口约束。上游核验失败会主动清除旧缓存；缓存缺失/损坏/过期时，来源专用详情 `503`，列表/搜索则返回 `status=partial`、`failed_sources=["official_source_verification"]`，仍展示经数据库校验的平台已发布岗位，不把其余候选丢掉。来源截止日期已过也不展示。“最近核验过且未过截止日期”仍不等于雇主确认现在仍招人，故有效性仍标 `unknown`。
- `source_csb_<jobid>` 是只读目录 ID，保留给来源专用层；L3/L4 `/api/match/run`、`/api/report/generate`、页面 Pipeline 的匹配/报告入口在建任务前拒绝它。`job_catalog_publication` 及旧 CLI 也不得发布这个保留前缀。即使客户端改 ID，L3/L4 仍只认独立的可评分发布仓库。
- 正式可评分岗位保持原有 `job_catalog_publication` 版本、人工确认、来源门禁和 L3/L4 `policy_estimate`，不由 source-only 条目自动升级。管理员未来可基于新证据另外走完整评分发布，但不会原地更改历史 source-only 版本。

## 验收要求

真实 Flask HTTP、MySQL/Redis 以唯一合成官方记录和固定 feed 返回测试：学生/匿名不能批准；摘要变化、来源不存在/变化/过期、重复版本拒绝；成功审核与审计同事务；公开列表/搜索/详情显示原文与不可匹配标志；L3/L4 在插任务前拒绝，`match_result`、`career_report` 保持 0；撤回后目录不回退。单独只读核对当前官方 50368 记录仍可在 feed 找到、摘要未变，不能把只读探测记为人工批准或真实发布。明确旧 MySQL volume 的加表迁移与回退边界。

## 当前 HTTP 契约

- `GET /api/admin/job-reviews` 与 `/<review_id>`：正式管理员只读查看摘要与原审查，不能凭模型结果自动发布。
- `POST /api/admin/job-source-listings`：正式管理员提交 `review_id`、`listing_version`、`expected_review_sha256`、`expected_source_record_sha256`、`confirm_source_only: true`。服务端只从固定 CSB feed 重新核对同一原文，不接受浏览器上传的岗位正文或来源地址；成功 `201` 返回独立目录 ID、版本行 ID、摘要及不可评分标记。摘要/原文变化 `409`，来源不可核验 `503`，未登录 `401`、非管理员 `403`。
- `POST /api/admin/job-source-listings/<id>/revoke`：`expected_content_sha256` 和 `reason` 必填，撤回与审计同事务，公开目录立即不再读取最新版；不会回退旧版。
- `GET /api/admin/job-source-listings?limit=&offset=` 与 `GET /<id>`：正式管理员可回看所有版本（含已撤回）、摘要、原文、来源核验和批准账号；详情逐次校验不可变快照摘要，普通用户不可读，响应禁止缓存。
- 公共 `GET /api/profile/job/list`、`/search`、`/<source_csb_id>`：source-only 项单独标注 `publication_status=source_only`、`match_eligible=false`、`scoring_status=not_supported`，详情的 `source_record` 才包含官方原文；`portrait_json=null`，不伪装为评分画像。若独立目录完整性校验失败，列表/搜索 `503`，详情 `503`，不是空结果。
- 正式管理员批准时从固定上游重取并可预热 Redis，`verification_cache=ready|pending` 是缓存状态，**不改变批准事务**；Beat 每 60 秒向 `cpa.pipeline` 队列派发 `cpa.catalog.refresh_source_feed`，独立 worker 获取 feed、只存每条记录摘要及核验时间，TTL 不超过 300 秒。必须同时运行 Beat、同队列 worker 与 Redis；只启动 Flask 不会后台刷新。`source_fetched_at` 保留批准时抓取时间，`source_verified_at` 是最近一次成功后台核验时间，不再等于当前 HTTP 请求时间。缓存失效/刷新失败时不会用旧原文冒充已核验；列表/搜索的部分结果需提示用户稍后重试，`retry_after_seconds=60`。这是有界新鲜度而非实时在招保证。
- `POST /api/match/run`、`/api/report/generate`、`/api/profile/student/pipeline` 的 L3/L4 对 `source_csb_` 保留前缀返回 `409` 与 `code=job_source_only_unscored`，在创建任何任务或报告 ID 之前拒绝。独立评分发布表与旧本机 CLI 也拒绝该保留前缀。

## 已有 MySQL volume 迁移

`backend/scripts/mysql/init_mysql.sql` 只作用于新库。升级既有 volume 时，先备份数据库，并在维护窗口以 `mkg` 环境、从 `backend/` 目录运行以下只加表 SQL；DDL 是幂等的，不修改原有评分发布表：

```powershell
python -B -c "from pathlib import Path; from app.services.db import get_mysql_client; get_mysql_client().execute_write(Path('scripts/mysql/migration_job_source_listing.sql').read_text(encoding='utf-8'))"
```

随后检查 `SHOW TABLES LIKE 'job_catalog_source_listing'`，再启用新后端。若该表未创建，公共目录读会报不可用，而不是无声遗漏 source-only 岗位。回退应用时可保留此独立表，旧版应用不消费它；不要为回退而直接删表或把 source-only 记录转写到 `job_catalog_publication`。
