# 已发布岗位只读目录合同

日期：2026-10-10。此合同服务于简历诊断报告的动态岗位层和主 Agent 资源读取；不改变 L3 匹配的评分门禁。

## 权威边界

`backend/app/services/job_catalog_listing.py` 的 `list_published_job_candidates(mysql, limit=20, offset=0)` 只读取 `job_catalog_publication`。岗位按 `job_portrait_id` 取最新发布行；最新版本被撤回时**不回退**旧版。对每个返回岗位复用 `read_published_job` 验证 `review_state=approved`、原文/逐字段证据结构与 `content_sha256`。`job_catalog_review`（含模型审查）和旧 `job_posting` 均不直接成为已发布岗位。无需用户 owner；这是公共目录，但用户的简历与匹配数据仍分别受 Principal 约束。

结果包含 `status=ready|empty|unavailable`、`total`、`items`、`catalog_version`、`catalog_latest_published_at`、`has_more`、`limit`、`offset`。单页最多 50 条。每条仅包含岗位 ID、发布版本与摘要、名称、来源 URI、来源获取/审核/发布时间、来源许可、`publication_status=published` 和 `vacancy_validity=unknown`。当前 schema 没有可信招聘截止日或实时在招状态，**已发布不等于今天仍招聘**；不要派生 `is_active=true`、公司、地点或更新时间。`catalog_version` 是目录状态变更提示，不是授权或事务快照。使用岗位做更强结论前须重查当前发布版本、来源有效性和所需证据。

空目录仍返回稳定 `catalog_version` 和 `status=empty`，简历事实报告可以独立完成；后续岗位发布/撤回后重新读取动态层即可，不应使原报告正文失效。数据库不可用或目录行校验失败返回 `status=unavailable`、空 items 和 reason，不以旧缓存冒充权威记录。

目录读取会在组装页面前后比较发布状态标记；并发发布或撤回让标记变化时返回 `status=unavailable,reason=catalog_changed_during_read`、空 items，不把混合时间点的页面和旧 `catalog_version` 一起交给调用方。这是可重试的读冲突，不是跨多个分页请求的事务快照；调用方仍须处理分页期间的版本变化。

## 旧 Profile 岗位路由的只读边界（2026-10-13）

`GET /api/profile/job/list`、`/search`、`/<portrait_id>` 现以此发布目录为来源，不再把 `job_portrait_4d` 或 `job_posting` 原始行直接当作公开岗位。列表和搜索只投影已发布摘要，搜索仅匹配已审核岗位名；详情在读取时再次校验该 ID 最新发布版本及快照摘要，返回已批准快照，没有有效发布则 404。最新版撤回时不回退到旧版。目录不可用时列表、搜索、分类返回 503，不能用空结果伪装系统正常。

另有独立的 `source_only` 官方来源展示层：公开列表和搜索会在已发布摘要后附上通过本次来源核验的条目，明确标记不可评分/匹配，不能把它们解释成评分发布。`/api/profile/job/search` 的 `keyword` 去除首尾空白后必须非空；纯空白请求返回 400，且不触发目录读取。搜索接受 `limit`、`offset`，按匹配后的合并目录分页，返回 `count`、`total`、`limit`、`offset`、`has_more`；因此前页由可评分岗位占满时，后页的 `source_only` 岗位仍可到达。搜索与列表每次请求都重新读取目录，`offset` **不是**跨请求事务快照；翻页期间若发布、撤回或官方原文变化，客户端须从第一页重查。

2026-10-14 起，`source_only` 的“本次来源核验”含义为读取最长 5 分钟的 Redis 后台核验摘要，公共 HTTP 不直接等待上游。缓存/上游不可用时列表和搜索保留已验证的 `published` 候选，返回 `success=true,status=partial,failed_sources=["official_source_verification"],retry_after_seconds=60`；正常命中/无命中分别标 `complete|empty`。来源专用详情在缓存不可用时仍为 503，不把未核验原文作为详情返回。Redis 的公共摘要不赋予岗位发布/评分权限，详见 `docs/specs/source-only-job-catalog.md`。

发布快照目前没有可信的 `job_category`、公司或薪资字段；`/api/profile/job/categories` 因此返回空分类和 `reason=published_catalog_has_no_category_field`，不读取未发布的 `job_posting`。前端旧 fixture 的 `title`、四维权重及分类/公司字段不等于现行公开发布合同；如需这些字段，应先在来源审核/发布合同中建立可验证证据，再调整接口与页面映射。当前实现为保持旧路由兼容，会逐页读取已校验目录再做名称筛选；大目录性能和目录变动时的重试仍需独立验收。

## 实测

本机 MySQL 只读查询：`job_catalog_publication` **0** 行，`job_catalog_review` **5** 行（审查不是发布）。`list_published_job_candidates(get_mysql_client())` 返回 `empty/total=0/items=[]`，没有把审查候选伪装为岗位。单元测试覆盖后续发布刷新、最新版撤回不回退、分页、摘要篡改；opt-in 真实 MySQL 只读测试：

```powershell
$env:CPA_TEST_LIVE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_job_catalog_listing.py
```

结果：5 passed（含一次在页面读取中途模拟并发发布的拒绝测试）。此测试不调用模型；真库夹具只验证合成发布/撤回和读取合同，不证明任何真实岗位可供推荐；真实岗位采集、审查和发布时间真实性仍是独立链路。

2026-10-13 搜索补验：`CPA_TEST_LIVE_MYSQL=1` 下执行 `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -q backend/tests/test_profile_job_search_blank_keyword_http.py --tb=short`，结果 3 passed。真实 socket HTTP 与 MySQL 验证纯空白关键字拒绝、已发布和 `source_only` 合并结果的第二页可达，并回查唯一夹具删除为 0；官方 feed 是固定合成记录替身，**不证明真实在招状态**。
