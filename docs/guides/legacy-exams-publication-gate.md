# 旧技能自评岗位推荐的发布来源门禁

`POST /api/pipeline/ext/exams` 是仍注册的旧技能自评路由。修正前，它直接用 `job_portrait_4d.position_name` / `portrait_json` 的 `LIKE` 结果作岗位推荐；该表包含未经管理员审核或发布的 L2 聚合画像。`GET /api/pipeline/ext/exams/<assessment_id>` 又原样回放已保存的角色名，撤回后仍可能展示。

## 当前契约

- 自评答案仍以登录 Principal 的 `student_id` 保存；`level` 是用户自报，不是可信能力评分。响应继续标为 `partial`、不生成权威四维画像。
- 推荐只扫描 `job_catalog_publication` 的**当前**已发布、人工审核、快照摘要已验证的版本，复用 `job_catalog_listing.list_published_job_candidates` 和 `job_publication.read_published_job`。不再读 `job_portrait_4d` 决定可推荐岗位。
- `target_role` 只匹配已发布快照中的岗位名称；用户自报的 `level >= 3` 技能只与已发布快照的 `required_skills[].name` 比较，不把来源文本或旧 L2 画像当成岗位资格条件。最多显示 5 个角色名；超过 500 个当前发布候选仍未扫完时返回 `recommendation_status=partial`、`recommendation_reason=catalog_scan_limit`，不声称搜索完整。
- 数据库的 `recommended_roles` JSON 存储 `source=reviewed_job_catalog`、推荐状态/原因，以及逐项的 `publication_source`、`job_portrait_id`、`publication_version`、`content_sha256` 和岗位名称；HTTP 仍返回角色名数组。历史读取会保留当时的“无匹配”和“目录故障”区别，并对有候选的项每次按当前发布版本和摘要重验；旧裸名称、撤回或被新版本替代的记录不会作为可信推荐回放。
- `recommendation_status` 区分 `ready`、`no_published_match`、`not_requested`、`partial` 和 `unavailable`。上游目录/摘要故障时保留自评记录，返回空推荐和明确不可用原因，不把故障伪装成无结果。岗位发布不证明广告当前仍在招，也不代表自报技能经过核验。

## 复现与验证

在仓库根目录使用 `mkg` Python，并将 `TEMP/TMP/TMPDIR` 指向 D 盘可写目录：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH='D:\AGeneral Workspace\cpa-agent\backend'
$env:TEMP='D:\AGeneral Workspace\cpa-agent\output'; $env:TMP=$env:TEMP; $env:TMPDIR=$env:TEMP
$env:CPA_TEST_LIVE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest -q backend/tests/test_pipeline_ext_exams_publication.py backend/tests/test_p0_legacy_ext_scope.py
```

修正前，同一真实 HTTP + MySQL 测试请求会把唯一的未发布 `job_portrait_4d` 测试岗位返回为推荐；修正后不再返回。另一个唯一正式发布测试岗位在 POST 和 GET 均可展示，撤回后 GET 立即清空并标记旧推荐不可用。测试只创建 `exam_*` 唯一夹具，最终精确删除自评、画像与发布记录，并核验无残留。不开启 `CPA_TEST_LIVE_MYSQL` 时只跑替身路径，不能据此声称真实数据库验收。

当前页面尚未接入该旧路由；这项修正只处理后端已注册路由的错误推荐和历史回放，不代表题库、正式技能评分、当前招聘有效性或整体职业推荐已完成。
