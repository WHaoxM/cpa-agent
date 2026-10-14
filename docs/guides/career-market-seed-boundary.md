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

此切片**不是**权威市场资料审核/发布能力。若要恢复薪资、供需或趋势，需按字段保存原文来源、观察期、地区、统计口径、摘要和审核版本；失效或撤回后公开读必须降级。`/api/career/infer-role` 目前仍把种子需求等级/入门难度用作次级加权并回传等级，`/api/career/assessment` 的历史自评推荐、收藏市场卡等也读取旧 `career_insights`；这些链路还需分别审计，不能因本页读侧门禁就宣布全平台市场结论可信。当前运行的 `:5001` 尚需按维护流程部署新代码后才会改变行为。
