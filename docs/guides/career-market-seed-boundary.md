# 职业方向冷启动数据与市场事实边界

`career_insights` 是职业方向冷启动目录，不是经核验的当前招聘市场统计。现有 MySQL 表只有 `updated_at`，没有量化主张对应的来源 URL、观察期、地区、口径或审核记录。`updated_at` 仅表示数据库行被修改，不是市场数据采集时间。

## 已复现的断点

2026-10-14 在本机正式后端 `:5001` 只读 GET：

- `/api/career/role-intro?role=AI%20Infra工程师` 原 200，`summary/outlook` 直接输出“7个岗位抢1个人”。
- `/api/career/insights/大模型算法工程师` 原 200，输出“2026占比30%-40%”、薪资 `25–50K`、需求等级等，但无来源、时间和地区。

这些断言来自 `backend/scripts/mysql/seed_cold_start_1.sql`，并非当前已发布岗位的统计。真实 HTTP 模式的能力图谱侧栏会读取 `role-intro`，所以不是仅在废弃脚本里的问题。

## 当前读侧门禁

`/api/career/landscape`、`/insights` 列表/详情、`/role-intro` 和 `/explore` 均保留职业名称、分类、技能、示例工作步骤等目录结构，但不把旧种子的薪资、需求、增长、热门地区/行业、竞争门槛或自由文本 `description` 当市场事实输出。后者可能在一句话中夹入无法核验的比例和供需数字，不能靠正则删掉部分数字就声称剩余内容可信。

兼容既有前端数据形状：列表/详情的缺证字段为 `null`，全景的 `avg_salary_range` 为空字符串；`role-intro` 的 `summary/outlook` 为空、`topRegions/requirements` 为空数组，仍有 `title/skills/responsibilities`。响应外层有 `status=partial`、`market_data_status=unverified_seed`、`source_coverage`、`unavailable_fields` 和 `next_action=await_verified_market_source`。`success:true` 表示请求成功，不表示市场数据完整或可信。用旧需求等级筛选 `/insights` 会返回 409 `market_filter_unverified`；关键词检索和排序不再使用未核验的描述、需求或薪资。

原 Redis 键可能存有旧的市场断言；相关五个读入口使用版本化新键，不从旧缓存读取。MySQL 故障下的全景返回 `data=[]/count=0/status=partial/source=unavailable`，不再用硬编码角色、数量或薪资伪造目录。

## 验证与剩余工作

`backend/tests/test_career_market_seed_http.py` 在隔离 Flask blueprint 的真实 TCP socket 上以同形冷启动行覆盖四类读入口，断言量化断言消失且结构/技能保留；先红后绿。`CPA_TEST_LIVE_MYSQL=1` 时另经真实本机 MySQL **只读**回查当前 `AI Infra工程师` 行并通过独立 socket 验证 `role-intro`/详情；不调用 app factory 的 schema ensure，不修改正式服务或数据库。命令：

```powershell
$env:TEMP='D:\AGeneral Workspace\cpa-agent\output'; $env:TMP=$env:TEMP; $env:TMPDIR=$env:TEMP
$env:CPA_TEST_LIVE_MYSQL='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_career_market_seed_http.py -q --tb=short
```

此切片**不是**权威市场资料审核/发布能力。若要恢复薪资、供需或趋势，需按字段保存原文来源、观察期、地区、统计口径、摘要和审核版本；失效或撤回后公开读必须降级。`/api/career/infer-role` 的后续切片已去除种子需求等级、入门难度和技能市场热度的评分权重与回传，保留技能候选并标明部分结果，详见 [职业方向来源覆盖](career-role-inference-source-coverage.md)。`/api/career/assessment` 的页面输入与历史自评推荐门禁见下节；收藏市场卡的独立门禁见再下节。不能因这些读侧门禁就宣布全平台市场结论可信。部署时须重启运行中的后端进程并复核真实 HTTP 响应。

## 技能自评的页面输入与历史回读

现有 Exams 页面向 `POST /api/career/assessment` 送 `assessed_skills: string[]` 弱项名称和 `assessed_abilities: {题目ID: 0..100}` 分数。后端现在将其作为 `gap_only` 差距提示保存；即使弱项数组为空，只要有合法答题分数也可提交。返回 `status=partial`、`named_mastery_levels=unavailable`，不擅自把弱项推断为「不会」或「了解」，也不据此伪造已掌握技能及职业推荐。旧 `{skill,level}[]` 输入仍可提交；有熟练技能时只按 `skill_taxonomy.related_roles` 关联数推荐，不按未核验的薪资、需求排序，`market_data_status=not_used`。关联职业同属未经审核的冷启动结构资料，若进入推荐则标 `partial_reasons=unverified_seed_catalogue`，不是可信岗位适配分。

最新自评 GET 使用新版本缓存键；历史记录里已保存的 `avg_salary*`、`demand_level` 等旧推荐字段在响应前剔除并标 `legacy_unverified_removed`，不会从旧 Redis 缓存复活。先红后绿测试见 `backend/tests/test_career_assessment_page_http.py`，包含现有页面原样请求的独立 TCP、真实 MySQL 写入→回读→精确清理，以及真实 `JSON_CONTAINS` 技能关联 SQL。严格身份模式仍要求正式登录 Principal；此切片不把客户端传来的 `student_id` 当身份，也不把页面自选 `target_role` 冒充服务器推荐或持久结论。

## 收藏职业卡的独立读侧门禁

`GET /api/target-role-market/<role>` 过去在 `job_posting` 无匹配时把 `career_insights` 种子的薪资、需求、热门地区、行业和自由描述直接展示为市场卡；有匹配时则聚合未经正式发布审核的原始 L0 岗位，并把表中「元/月」薪资直接拼接成 `K`。2026-10-14 正式 `:5001` 对 `AI Infra工程师` 的只读 GET 曾回传 `25K-50K`、`极高` 及「7个岗位抢1个人」，而真库 `job_posting=0`、已发布岗位 `=0`。这不是市场统计。

新读路径只从 `career_insights` 取 `role_name/core_skills` 作为冷启动方向目录，不查询原始 `job_posting`。卡片保留名称与技能，市场字段用 `null`、空数组或 `—` 显式不可用，`sampleJobs` 不再把职业名冒充真实岗位；响应返回 `status=partial`、`market_data_status=unverified_seed`、`unavailable_fields` 和下一步动作。无目录行时 `status=no_result/data=null`。只有未来建立字段级可核验来源、样本/时间/地域口径与审核撤回链，才可恢复量化市场字段。已发布岗位数量本身也不能直接当作市场需求量。

回归为 `backend/tests/test_favorites_market_seed_http.py`：真实 TCP + 带未发布原始岗位的假读库先红后绿，验证没有 `job_posting` 查询或无来源字段输出；设置 `CPA_TEST_LIVE_MYSQL=1` 后，通过同一生产 blueprint 的独立本机 socket 只读访问真实 MySQL，不运行 app factory schema ensure，也不写入测试行。此测试只能证明本机现有种子不会由**新代码**泄露；运行中的服务也须重启并验证实际返回。

**现有页面约束（本轮不改前端）**：`cpa-xingtu/src/api/mappers.ts` 的 `mergeTargetRoleMarket` 与 `Favorites.vue` 的本地 fallback 会把后端不可用字段重新补成演示薪资/需求/趋势；当前后端门禁不能被称为真实收藏页视觉修复。不能为压过这个回填逻辑而在后端伪造非零薪资、需求数或假城市。若坚持仅改后端，下一步必须先建立经核验且覆盖卡片字段的真实市场资料来源与审核/撤回机制；当前不存在这种数据。
