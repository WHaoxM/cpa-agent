# 已发布岗位只读目录合同

日期：2026-10-10。此合同服务于简历诊断报告的动态岗位层和主 Agent 资源读取；不改变 L3 匹配的评分门禁。

## 权威边界

`backend/app/services/job_catalog_listing.py` 的 `list_published_job_candidates(mysql, limit=20, offset=0)` 只读取 `job_catalog_publication`。岗位按 `job_portrait_id` 取最新发布行；最新版本被撤回时**不回退**旧版。对每个返回岗位复用 `read_published_job` 验证 `review_state=approved`、原文/逐字段证据结构与 `content_sha256`。`job_catalog_review`（含模型审查）和旧 `job_posting` 均不直接成为已发布岗位。无需用户 owner；这是公共目录，但用户的简历与匹配数据仍分别受 Principal 约束。

结果包含 `status=ready|empty|unavailable`、`total`、`items`、`catalog_version`、`catalog_latest_published_at`、`has_more`、`limit`、`offset`。单页最多 50 条。每条仅包含岗位 ID、发布版本与摘要、名称、来源 URI、来源获取/审核/发布时间、来源许可、`publication_status=published` 和 `vacancy_validity=unknown`。当前 schema 没有可信招聘截止日或实时在招状态，**已发布不等于今天仍招聘**；不要派生 `is_active=true`、公司、地点或更新时间。`catalog_version` 是目录状态变更提示，不是授权或事务快照。使用岗位做更强结论前须重查当前发布版本、来源有效性和所需证据。

空目录仍返回稳定 `catalog_version` 和 `status=empty`，简历事实报告可以独立完成；后续岗位发布/撤回后重新读取动态层即可，不应使原报告正文失效。数据库不可用或目录行校验失败返回 `status=unavailable`、空 items 和 reason，不以旧缓存冒充权威记录。

## 实测

本机 MySQL 只读查询：`job_catalog_publication` **0** 行，`job_catalog_review` **5** 行（审查不是发布）。`list_published_job_candidates(get_mysql_client())` 返回 `empty/total=0/items=[]`，没有把审查候选伪装为岗位。单元测试覆盖后续发布刷新、最新版撤回不回退、分页、摘要篡改；opt-in 真实 MySQL 只读测试：

```powershell
$env:CPA_TEST_LIVE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_job_catalog_listing.py
```

结果：4 passed。此测试不调用模型、不发布岗位、不证明任何真实岗位可供推荐；真实岗位采集、审查和发布时间真实性仍是独立链路。
